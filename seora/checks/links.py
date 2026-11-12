"""Links: internal and external count, broken and redirecting links, nofollow, empty anchor text.

Each distinct link address is checked once (HEAD, then a small GET if HEAD fails or is refused)
through ctx.try_fetch, with the same limits as images (see _fetchmany.py).
"""

from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import config

from ..urls import comparable
from ._fetchmany import fetch_many
from ._issue import clip, issue

GROUP = "Links"

EXAMPLES = 5
HTML_ACCEPT = "text/html,application/xhtml+xml,*/*;q=0.8"
# Answers that usually mean "no robots allowed" rather than "page missing" (999 is LinkedIn's)
REFUSED = {401, 403, 429, 999}
# Fetch failures that mean the link is broken for every visitor
DEAD_CODES = {"dns": "site not found", "tls_cert": "certificate problem", "refused": "connection refused"}
NOFOLLOW = {"nofollow", "ugc", "sponsored"}


@dataclass
class Result:
    status: int = None         # final status after redirects (None: no answer)
    code: str = None           # fetch failure code when there was no answer
    chain: list = None         # [(url, status)] redirects followed
    final: str = ""


def run(ctx):
    links, other = collect(ctx)
    if not links:
        return [issue("links-none", "notice", "Page has no links",
                      "Search engines find pages by following links, and visitors need a way to go on from here."
                      + (f" ({other} email or phone links aren't counted.)" if other else ""),
                      "Link to related pages on your site, such as the home page, categories or similar articles.")]
    results, attempted, skipped = check_all(ctx, links)
    return (summary(ctx, links, other) + broken(links, results, attempted, skipped)
            + redirects(links, results) + nofollow(links) + anchor_text(links))


# ---------- gathering ----------

def _site(host):
    return host[4:] if host.startswith("www.") else host


def _no_fragment(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


@dataclass
class Link:
    url: str        # absolute, without #fragment
    internal: bool
    rel: set
    text: str       # anchor text, or what stands in for it (image alt, aria-label, title)
    href: str


def collect(ctx):
    """Links to web pages, and the count of other links (mailto:, tel:...). Same-page #anchors are left out."""
    site = _site((urlsplit(ctx.final_url).hostname or "").lower())
    this_page = comparable(ctx.final_url)
    links, other = [], 0
    for a in ctx.page.anchors:
        href = (a.get("href") or "").strip()
        url = a.get("url") or ""
        if not href or href.startswith("#"):
            continue
        scheme = urlsplit(url).scheme.lower() if url else ""
        if scheme not in ("http", "https"):
            if scheme and scheme != "javascript":
                other += 1
            continue
        try:
            clean = _no_fragment(url)
            host = (urlsplit(clean).hostname or "").lower()
        except ValueError:
            continue
        if comparable(clean) == this_page and "#" in href:
            continue  # a jump within this page
        text = a["text"] or a["img_alt"] or (a.get("aria_label") or "").strip() or a.get("labels") or (a.get("title") or "").strip()
        links.append(Link(url=clean, internal=_site(host) == site, rel=set((a.get("rel") or "").lower().split()),
                          text=text, href=href))
    return links, other


def short(url, internal):
    """How a link address is shown in reports: the path for this site, host and path for others."""
    p = urlsplit(url)
    path = (p.path or "/") + (f"?{p.query}" if p.query else "")
    return clip(path if internal else (p.hostname or "") + (path if path != "/" else ""), 70)


def _examples(items, label):
    names = list(dict.fromkeys(label(i) for i in items))
    shown = ", ".join(names[:EXAMPLES])
    return shown + (f" and {len(names) - EXAMPLES} more" if len(names) > EXAMPLES else "")


def _plural(n, word):
    if n == 1:
        return f"1 {word}"
    return f"{n} {word}{'es' if word.endswith(('s', 'sh', 'ch', 'x')) else 's'}"


# ---------- checking ----------

def probe(ctx, url):
    r, code = ctx.try_fetch(url, method="HEAD", accept=HTML_ACCEPT, timeout=config.ASSET_TIMEOUT)
    if r is None or r.status >= 400:
        # Some servers refuse or mishandle HEAD; ask again with a small GET before calling a link broken
        g, gcode = ctx.try_fetch(url, method="GET", accept=HTML_ACCEPT, max_bytes=16_384, timeout=config.ASSET_TIMEOUT)
        if g is not None or r is None:
            r, code = g, gcode
    if r is None:
        return Result(code=code)
    return Result(status=r.status, chain=[(h.url, h.status) for h in r.chain], final=r.url)


def check_all(ctx, links):
    # Internal links first: a broken link to your own page matters most
    ordered = [l.url for l in links if l.internal] + [l.url for l in links if not l.internal]
    this_page = comparable(ctx.final_url)
    ordered = [u for u in ordered if comparable(u) != this_page]  # this page was just fetched
    return fetch_many(ctx, ordered, probe, config.MAX_LINKS_CHECKED)


# ---------- checks ----------

def summary(ctx, links, other):
    internal = sum(1 for l in links if l.internal)
    external = len(links) - internal
    distinct = len({l.url for l in links})
    extra = f" Another {other} are email or phone links." if other else ""
    detail = f"{_plural(internal, 'link')} to this site and {_plural(external, 'link')} to other sites, going to {distinct} different addresses.{extra}"
    if not internal:
        return [issue("links-no-internal", "warning", "No links to other pages on this site", detail
                      + " Search engines find your other pages by following links, and visitors have nowhere to go next.",
                      "Link to related pages on your own site, for example in a menu, the text, or a 'related' section.")]
    return [issue("links-count", "pass", f"Page has {_plural(len(links), 'link')}", detail)]


def broken(links, results, attempted, skipped):
    seen = {}
    for l in links:
        seen.setdefault(l.url, l)
    dead, refused, unknown, ok = [], [], [], 0
    for url, l in seen.items():
        if url not in attempted:
            continue
        r = results.get(url)
        if r is None or (r.status is None and r.code not in DEAD_CODES):
            unknown.append(l)
        elif r.status is None:
            dead.append((l, DEAD_CODES[r.code]))
        elif r.status in REFUSED:
            refused.append((l, r.status))
        elif r.status >= 400:
            dead.append((l, str(r.status)))
        else:
            ok += 1
    cap_note = (f" {skipped} more {'address was' if skipped == 1 else 'addresses were'} not checked (limit {config.MAX_LINKS_CHECKED})."
                if skipped else "")
    out = []
    if dead:
        dead.sort(key=lambda x: not x[0].internal)
        n_int = sum(1 for l, _ in dead if l.internal)
        where = f" ({n_int} on this site)" if n_int and n_int < len(dead) else (" on this site" if n_int else " to other sites")
        out.append(issue("links-broken", "error", f"{_plural(len(dead), 'broken link')}{where}",
                         _examples(dead, lambda x: f"{short(x[0].url, x[0].internal)} ({x[1]})")
                         + ". Visitors hit an error page, and search engines see a site that isn't looked after." + cap_note,
                         "Fix each link to point to a page that exists, or remove it. If a page moved, add a 301 redirect from its old address."))
    if refused:
        out.append(issue("links-refused", "notice", f"{_plural(len(refused), 'link')} refused Seora's check",
                         _examples(refused, lambda x: f"{short(x[0].url, x[0].internal)} ({x[1]})")
                         + ". Some sites (LinkedIn, sites behind bot protection) turn away automated checks, so these may work fine for people.",
                         "Open these links in a browser to make sure they work."))
    if unknown:
        out.append(issue("links-unchecked", "notice", f"{_plural(len(unknown), 'link')} couldn't be checked",
                         _examples(unknown, lambda l: short(l.url, l.internal)) + " didn't answer in time or couldn't be reached.",
                         "Open these links in a browser to make sure they work."))
    if not dead:
        out.append(issue("links-ok", "pass", "No broken links found",
                         f"{_plural(ok, 'address')} answered normally.{cap_note}"))
    return out


def redirects(links, results):
    seen, moved = set(), []
    for l in links:
        r = results.get(l.url)
        if l.url in seen or r is None or not r.chain or r.status is None or r.status >= 400:
            continue
        seen.add(l.url)
        moved.append((l, r))
    if not moved:
        return [issue("links-redirect-ok", "pass", "No links go through redirects")] if results else []
    moved.sort(key=lambda x: not x[0].internal)
    n_int = sum(1 for l, _ in moved if l.internal)
    detail = _examples(moved, lambda x: f"{short(x[0].url, x[0].internal)} → {short(x[1].final, x[0].internal)} ({x[1].chain[0][1]})")
    if n_int:
        whom = ("It goes" if len(moved) == 1 else "All of them go" if n_int == len(moved)
                else "1 of them goes" if n_int == 1 else f"{n_int} of them go")
        detail += f". {whom} to this site. Each redirect adds a delay for visitors and a step for search engines."
    else:
        detail += ". Each redirect adds a delay for visitors."
    title = "1 link goes through a redirect" if len(moved) == 1 else f"{len(moved)} links go through a redirect"
    return [issue("links-redirect", "notice", title, detail,
                  "Change these links to point straight to the final address shown after the arrow.")]


def nofollow(links):
    internal = [l for l in links if l.internal and l.rel & NOFOLLOW]
    external = sum(1 for l in links if not l.internal and l.rel & NOFOLLOW)
    if internal:
        return [issue("links-nofollow-internal", "warning", f"{_plural(len(internal), 'link')} to this site marked nofollow",
                      _examples(internal, lambda l: short(l.url, True))
                      + ". nofollow tells search engines not to pass credit through a link, which you rarely want for your own pages.",
                      'Remove rel="nofollow" from links to your own pages. To keep a page out of search results, use noindex on that page instead.')]
    detail = f"{external} link{'s' if external != 1 else ''} to other sites {'are' if external != 1 else 'is'} marked nofollow, ugc or sponsored, which is fine." if external else ""
    return [issue("links-nofollow-ok", "pass", "No links to this site are marked nofollow", detail)]


def anchor_text(links):
    empty = [l for l in links if not l.text]
    if empty:
        return [issue("links-empty-text", "warning", f"{_plural(len(empty), 'link')} without text",
                      _examples(empty, lambda l: short(l.url, l.internal))
                      + ". Search engines use link text to understand the page it points to, and screen readers have nothing to read out.",
                      "Give each link words that say where it goes. For icon or image links, add alt text to the image or an aria-label to the link.")]
    return [issue("links-text-ok", "pass", "All links have text")]
