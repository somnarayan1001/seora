"""Page loading, measured by the server: time to first byte, download time, HTML weight, the number
and weight of scripts, stylesheets and images, render-blocking scripts, and the heaviest files.

Seora doesn't run the page in a browser, so it can't time what visitors see (LCP, CLS). These checks
cover what can be measured from the files themselves. Image sizes are reused from the Images group.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

import config

from . import images as images_group
from ._fetchmany import fetch_many
from ._issue import clip, issue
from ._sizes import probe_size

GROUP = "Page loading"

# ms the server takes to answer once connected. Connection setup isn't graded: it depends mostly on the
# distance between Seora and the site, not on the site. 600 ms is Lighthouse's limit for server response time.
SERVER_GOOD, SERVER_POOR = 600, 1500
DOWNLOAD_SLOW = 1000                           # ms to download the HTML
HTML_BIG, HTML_HUGE = 300_000, 1_000_000       # bytes of HTML (unzipped)
TOTAL_BIG, TOTAL_HUGE = 2_000_000, 5_000_000   # bytes for the first load (what's downloaded before scrolling)
JS_BIG, JS_HUGE = 500_000, 1_000_000           # bytes of JavaScript over the network
MANY_FILES = 80
HEAVIEST = 5
SCRIPT_TYPES = {"", "text/javascript", "application/javascript", "application/ecmascript", "text/ecmascript"}


@dataclass
class File:
    kind: str        # "script", "style" or "image"
    url: str
    lazy: bool = False
    size: int = None


def run(ctx):
    files = collect(ctx)
    unmeasured = measure(ctx, files)
    return (server_time(ctx) + html_weight(ctx) + page_weight(ctx, files, unmeasured)
            + script_weight(files) + render_blocking(ctx) + heaviest(ctx, files))


# ---------- gathering and measuring ----------

def collect(ctx):
    page, files = ctx.page, []
    for l in page.links:
        if "stylesheet" in (l.get("rel") or "").lower().split() and (l.get("url") or "").startswith(("http://", "https://")):
            files.append(File("style", l["url"]))
    for s in page.scripts:
        if (s.get("url") or "").startswith(("http://", "https://")) and (s.get("type") or "").strip().lower() in SCRIPT_TYPES | {"module"}:
            files.append(File("script", s["url"]))
    for i in images_group.collect(page):
        if i.url.startswith(("http://", "https://")):
            lazy = (i.attrs.get("loading") or "").strip().lower() == "lazy" or i.js_lazy
            files.append(File("image", i.url, lazy=lazy))
    # each address once; an image that is both lazy and not counts as loaded straight away
    seen = {}
    for f in files:
        if f.url in seen:
            seen[f.url].lazy = seen[f.url].lazy and f.lazy
        else:
            seen[f.url] = f
    return list(seen.values())


def measure(ctx, files):
    """Fill in sizes. Returns how many files couldn't be measured (no answer, no size, or over the limits)."""
    code = [f.url for f in files if f.kind != "image"]
    pics = [f.url for f in files if f.kind == "image"]
    accept = lambda u: images_group.ACCEPT if u in set(pics) else "*/*"
    results = {}
    for urls, cap in ((code, config.MAX_FILES_CHECKED), (pics, config.MAX_IMAGES_CHECKED)):
        got, _, _ = fetch_many(ctx, urls, lambda c, u: probe_size(c, u, accept(u)), cap)
        results.update(got)
    missing = 0
    for f in files:
        r = results.get(f.url)
        if r and r[0] is not None and r[0] < 400 and r[1]:
            f.size = r[1]
        else:
            missing += 1
    return missing


def _kb(n):
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def _ms(n):
    return f"{n / 1000:.1f} s" if n >= 1000 else f"{round(n)} ms"


def _name(url, deeper=False):
    """Short name for a file: its file name, with the host when the name says nothing (like "js"),
    or with its folder (deeper=True) to tell apart files that share a name."""
    p = urlsplit(url)
    parts = [s for s in p.path.split("/") if s]
    name = "/".join(parts[-2:]) if deeper and len(parts) > 1 else (parts[-1] if parts else "")
    if "." not in name.rsplit("/", 1)[-1]:
        name = f"{p.hostname}/{name}" if name else (p.hostname or url)
    return clip(name, 48)


# ---------- checks ----------

def server_time(ctx):
    r = ctx.response
    ttfb, download, connect = r.ttfb_ms, r.download_ms, r.connect_ms
    server = max(0.0, ttfb - connect)
    redirect_ms = max(0, r.total_ms - ttfb - download) if r.chain else 0
    detail = (f"First byte after {_ms(ttfb)}: {_ms(connect)} to connect, then {_ms(server)} for the server to answer. "
              f"Whole HTML after {_ms(ttfb + download)}."
              + (f" Redirects before it added {_ms(redirect_ms)}." if redirect_ms >= 50 else "")
              + " Connecting depends mostly on the distance between Seora and the site, so only the server's part is judged here.")
    out = []
    if server > SERVER_POOR:
        out.append(issue("ttfb-slow", "warning", f"Server is slow to answer ({_ms(server)})", detail
                         + " Nothing appears on screen until the first byte arrives.",
                         "Speed up the server: turn on page caching (or a caching plugin), put a CDN in front of the site, "
                         "reduce slow database queries, or move to faster hosting."))
    elif server > SERVER_GOOD:
        out.append(issue("ttfb-medium", "notice", f"Server takes a while to answer ({_ms(server)})", detail
                         + f" Under {_ms(SERVER_GOOD)} is good.",
                         "Page caching or a CDN usually brings this well under 600 ms."))
    else:
        out.append(issue("ttfb-ok", "pass", f"Server answers quickly ({_ms(server)})", detail))
    if download > DOWNLOAD_SLOW:
        out.append(issue("download-slow", "notice", f"The HTML takes {_ms(download)} to download",
                         f"After the first byte, the rest of the page took {_ms(download)} to arrive ({_kb(r.transfer_bytes or len(r.body))}).",
                         "Make the HTML smaller (see below) and make sure it's compressed. A CDN also helps."))
    return out


def html_weight(ctx):
    r = ctx.response
    size, sent = len(r.body), r.transfer_bytes or len(r.body)
    detail = f"{_kb(size)} of HTML" + (f", {_kb(sent)} over the network after compression." if sent < size else ".")
    if size > HTML_HUGE:
        return [issue("html-huge", "warning", f"The HTML is very large ({_kb(size)})", detail
                      + " Browsers must download and read all of it before showing the page properly, which is slow on phones.",
                      "Remove what isn't needed on first view: large inline scripts, styles and SVGs, embedded data, "
                      "and long lists that could be split over several pages or loaded on request.")]
    if size > HTML_BIG:
        return [issue("html-big", "notice", f"The HTML is large ({_kb(size)})", detail
                      + f" Most fast pages have less than {_kb(HTML_BIG)} of HTML.",
                      "Move inline scripts and styles into separate files, and remove markup that isn't shown.")]
    return [issue("html-ok", "pass", f"The HTML is a good size ({_kb(size)})", detail)]


def page_weight(ctx, files, unmeasured):
    html = ctx.response.transfer_bytes or len(ctx.response.body)
    kinds = {"script": "script", "style": "stylesheet", "image": "image"}
    parts, first_load = [], html
    for kind, word in kinds.items():
        group = [f for f in files if f.kind == kind]
        if not group:
            continue
        total = sum(f.size or 0 for f in group)
        lazy = [f for f in group if f.lazy]
        text = f"{len(group)} {word}{'s' if len(group) != 1 else ''} ({_kb(total)})"
        if lazy:
            text += f", {len(lazy)} of them loaded only when scrolled to"
        parts.append(text)
        first_load += sum(f.size or 0 for f in group if not f.lazy)
    count = 1 + len(files)
    detail = (f"HTML {_kb(html)}" + ("; " + "; ".join(parts) if parts else "")
              + f". About {_kb(first_load)} is downloaded before scrolling.")
    if unmeasured:
        detail += f" {unmeasured} file{'s' if unmeasured != 1 else ''} couldn't be measured, so the real total is higher."
    detail += " Fonts and files that scripts load later aren't counted."
    out = []
    if first_load > TOTAL_HUGE:
        out.append(issue("weight-huge", "warning", f"The page is very heavy ({_kb(first_load)})", detail
                         + " On a typical phone connection this takes many seconds and uses a lot of mobile data.",
                         "Start with the heaviest files listed below: compress and resize images, lazy-load images further down, "
                         "and remove scripts the page doesn't need."))
    elif first_load > TOTAL_BIG:
        out.append(issue("weight-big", "notice", f"The page is heavy ({_kb(first_load)})", detail
                         + f" Fast pages usually stay under {_kb(TOTAL_BIG)}.",
                         "Look at the heaviest files listed below and shrink or remove the biggest ones."))
    else:
        out.append(issue("weight-ok", "pass", f"The page weight is fine ({_kb(first_load)})", detail))
    if count > MANY_FILES:
        out.append(issue("files-many", "notice", f"The page loads {count} files",
                         f"Counting the HTML, scripts, stylesheets and images in the HTML. Each file is a separate request, and many small ones add up.",
                         "Combine small scripts and stylesheets, use CSS or SVG icons instead of many small images, and lazy-load images further down."))
    return out


def script_weight(files):
    every = [f for f in files if f.kind == "script"]
    scripts = [f for f in every if f.size]
    total = sum(f.size for f in scripts)
    if not scripts:
        return []
    counted = f"{len(scripts)} of {len(every)} script files measured" if len(scripts) < len(every) else \
        f"{len(scripts)} script file{'s' if len(scripts) != 1 else ''}"
    detail = f"{counted}, {_kb(total)} over the network. Phones need time to run JavaScript as well as download it."
    if total > JS_HUGE:
        return [issue("js-huge", "warning", f"Very large amount of JavaScript ({_kb(total)})", detail,
                      "Remove plugins and scripts the page doesn't use, load each page's code only on that page, "
                      "and load chat widgets, videos and other extras only when someone uses them.")]
    if total > JS_BIG:
        return [issue("js-big", "notice", f"Large amount of JavaScript ({_kb(total)})", detail,
                      "Check which scripts the page really needs, and remove or delay the rest.")]
    return [issue("js-ok", "pass", f"The amount of JavaScript is fine ({_kb(total)})", detail)]


def render_blocking(ctx):
    blocking = [s for s in ctx.page.scripts
                if s.get("in_head") and s.get("src") and not s.get("async") and not s.get("defer")
                and (s.get("type") or "").strip().lower() in SCRIPT_TYPES]
    if blocking:
        names = list(dict.fromkeys(_name(s.get("url") or s["src"]) for s in blocking))
        shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
        return [issue("render-blocking", "notice",
                      f"{len(blocking)} script{'s' if len(blocking) != 1 else ''} in <head> {'hold' if len(blocking) != 1 else 'holds'} up the first display",
                      f"{shown}. The browser stops building the page until each one is downloaded and run.",
                      'Add defer to these <script> tags (or async for independent ones such as analytics), or move them to the end of <body>.')]
    return [issue("render-blocking-ok", "pass", "No scripts hold up the first display",
                  "Scripts in <head> use async or defer, or there are none.")]


def heaviest(ctx, files):
    measured = sorted((f for f in files if f.size), key=lambda f: -f.size)[:HEAVIEST]
    if not measured:
        return []
    words = {"script": "script", "style": "stylesheet", "image": "image"}
    names = [_name(f.url) for f in measured]
    clash = {n for n in names if names.count(n) > 1}
    detail = ", ".join(f"{_name(f.url, deeper=n in clash)} ({words[f.kind]}, {_kb(f.size)}{', lazy' if f.lazy else ''})"
                       for f, n in zip(measured, names))
    return [issue("heaviest", "pass", "Heaviest files", detail + ". Shrinking these first gives the biggest gain.")]
