"""Meta tags: title, description, canonical, robots, viewport, language, charset,
favicon, Open Graph, Twitter cards and JSON-LD structured data."""

import json
import re
from urllib.parse import urljoin, urlsplit

from .. import fetch as fetchmod
from ..parse import squash
from ..urls import comparable, is_absolute_http, same_page
from ._issue import clip, issue

GROUP = "Meta tags"

TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160


def run(ctx):
    out = []
    for check in (title, description, canonical, robots, viewport, lang, charset, favicon,
                  open_graph, twitter, json_ld):
        out += check(ctx)
    return out


# ---------- title ----------

def title(ctx):
    titles = [squash(t) for t in ctx.page.titles]
    if not titles:
        return [issue("title-missing", "error", "Page title is missing",
                      "There is no <title> tag. Search results show the title as the clickable headline.",
                      f"Add a <title> inside <head> that says what the page is about, in {TITLE_MIN}–{TITLE_MAX} characters.")]
    t = titles[0]
    if not t:
        return [issue("title-empty", "error", "Page title is empty",
                      "The <title> tag has no text, so search engines will make up a headline.",
                      f"Write a title of {TITLE_MIN}–{TITLE_MAX} characters inside the <title> tag.")]
    out = []
    if len(titles) > 1:
        out.append(issue("title-multiple", "warning", f"Page has {len(titles)} title tags",
                         "Search engines use the first one and ignore the rest, which may not be the one you meant.",
                         "Keep one <title> tag in <head> and remove the others."))
    n = len(t)
    if n < TITLE_MIN:
        out.append(issue("title-short", "warning", f"Page title is short ({n} characters)", f"“{clip(t)}”",
                         f"Lengthen it to {TITLE_MIN}–{TITLE_MAX} characters: name the main topic, then your brand."))
    elif n > TITLE_MAX:
        out.append(issue("title-long", "warning", f"Page title is long ({n} characters)",
                         f"“{clip(t)}”. Search results usually cut titles off after about {TITLE_MAX} characters.",
                         f"Shorten it to {TITLE_MAX} characters or fewer, with the most important words first."))
    else:
        out.append(issue("title-ok", "pass", f"Page title length is good ({n} characters)", f"“{clip(t)}”"))
    return out


# ---------- meta description ----------

def description(ctx):
    descs = [squash(d) for d in ctx.page.meta("description")]
    if not descs:
        return [issue("description-missing", "warning", "Meta description is missing",
                      "Search engines will pick a snippet from the page text for the search result, which may not sell the page well.",
                      f'Add <meta name="description" content="…"> in <head>: a {DESC_MIN}–{DESC_MAX} character summary that makes people want to click.')]
    d = descs[0]
    if not d:
        return [issue("description-empty", "warning", "Meta description is empty",
                      "The description tag is there but has no text.",
                      f"Write a {DESC_MIN}–{DESC_MAX} character summary of the page in its content attribute.")]
    out = []
    if len(descs) > 1:
        out.append(issue("description-multiple", "warning", f"Page has {len(descs)} meta descriptions",
                         "Search engines may use any of them, or none.",
                         "Keep one meta description and remove the others."))
    n = len(d)
    if n < DESC_MIN:
        out.append(issue("description-short", "warning", f"Meta description is short ({n} characters)", f"“{clip(d)}”",
                         f"Expand it to {DESC_MIN}–{DESC_MAX} characters: say what the page offers and why to click."))
    elif n > DESC_MAX:
        out.append(issue("description-long", "warning", f"Meta description is long ({n} characters)",
                         f"“{clip(d)}”. Search results usually cut it off after about {DESC_MAX} characters.",
                         f"Shorten it to {DESC_MAX} characters or fewer, with the key message first."))
    else:
        out.append(issue("description-ok", "pass", f"Meta description length is good ({n} characters)", f"“{clip(d)}”"))
    return out


# ---------- canonical ----------

def _canonical_headers(resp):
    """rel=canonical targets from HTTP Link headers."""
    found = []
    for value in resp.header_all("link"):
        for part in re.finditer(r"<([^>]*)>\s*((?:;\s*[^;,]+)*)", value):
            if re.search(r"""rel\s*=\s*"?[^";,]*\bcanonical\b""", part.group(2), re.I):
                found.append(part.group(1).strip())
    return found


def canonical(ctx):
    page = ctx.page
    tags = page.links_with_rel("canonical")
    header_hrefs = _canonical_headers(ctx.response)
    how_to = f'Add <link rel="canonical" href="{clip(ctx.final_url, 200)}"> in <head> (use this page\'s own full address unless it copies another page).'
    if not tags and not header_hrefs:
        return [issue("canonical-missing", "warning", "Canonical tag is missing",
                      "Without it, search engines guess which address is the main one when the same page is reachable in several ways (with tracking parameters, http and https, with and without www).",
                      how_to)]
    out = []
    hrefs = [(t.get("href") or "").strip() for t in tags]
    targets = [urljoin(ctx.final_url, h) if h else "" for h in hrefs]
    header_targets = [urljoin(ctx.final_url, h) for h in header_hrefs]
    every = targets + header_targets
    distinct = {comparable(u) for u in every if u}
    if len(distinct) > 1:
        out.append(issue("canonical-conflict", "error", "Canonical tags disagree",
                         f"{len(every)} canonical tags point to {len(distinct)} different addresses, so search engines may ignore all of them: "
                         + "; ".join(clip(u, 80) for u in sorted(distinct)[:3]),
                         "Keep a single canonical tag that points to the main address of this page."))
        return out
    if len(tags) > 1:
        out.append(issue("canonical-multiple", "warning", f"Page has {len(tags)} canonical tags",
                         "They point to the same address, so this works, but it suggests a template adds the tag twice.",
                         "Remove the extra canonical tags so there is only one."))
    if hrefs and not hrefs[0]:
        out.append(issue("canonical-empty", "error", "Canonical tag has no address",
                         "The canonical tag's href is empty, so search engines ignore it.", how_to))
        return out
    target = every[0]
    if not is_absolute_http(target):
        out.append(issue("canonical-invalid", "error", "Canonical tag address isn't a web address",
                         f"It points to “{clip(target, 100)}”.", how_to))
        return out
    if hrefs and not is_absolute_http(hrefs[0]):
        out.append(issue("canonical-relative", "notice", "Canonical tag uses a relative address",
                         f"“{clip(hrefs[0], 100)}”. It works, but a full address avoids mistakes when pages are copied or served under another domain.",
                         "Use the full address, starting with https://, in the canonical tag."))
    if same_page(target, ctx.final_url):
        out.append(issue("canonical-ok", "pass", "Canonical tag points to this page", clip(target, 150)))
        return out
    page_p, target_p = urlsplit(ctx.final_url), urlsplit(target)
    if page_p.scheme == "https" and target_p.scheme == "http":
        out.append(issue("canonical-http", "warning", "Canonical tag points to the insecure http:// version",
                         f"It points to {clip(target, 150)}.",
                         "Change the canonical tag to the https:// address."))
    elif comparable(target).rstrip("/") == comparable(ctx.final_url).rstrip("/"):
        out.append(issue("canonical-slash", "notice", "Canonical tag differs from this address only by a trailing slash",
                         f"Page: {clip(ctx.final_url, 120)}  Canonical: {clip(target, 120)}. Search engines treat these as different addresses.",
                         "Make the canonical tag match the address the page is actually served at, slash included."))
    else:
        out.append(issue("canonical-elsewhere", "notice", "Canonical tag points to a different page",
                         f"It points to {clip(target, 150)}. Search engines will usually list that address instead of this one. That is right for copies (print versions, tracking parameters) and wrong if this page should appear in search on its own.",
                         "If this page should appear in search results, change the canonical tag to this page's own address."))
    return out


# ---------- robots ----------

KNOWN_DIRECTIVES = {"all", "none", "noindex", "nofollow", "index", "follow", "noarchive", "nocache", "nosnippet",
                    "notranslate", "noimageindex", "unavailable_after", "max-snippet", "max-image-preview",
                    "max-video-preview", "indexifembedded", "noodp", "noydir"}


def _robots_directives(ctx):
    """[(directive, where)] for robots rules aimed at all crawlers or at Google."""
    found = []
    for name in ("robots", "googlebot"):
        for content in ctx.page.meta(name):
            for d in content.split(","):
                d = d.strip().lower()
                if d:
                    found.append((d, f'<meta name="{name}">'))
    for value in ctx.response.header_all("x-robots-tag"):
        agent = None
        for d in value.split(","):
            d = d.strip().lower()
            m = re.match(r"^([a-z0-9_-]+)\s*:\s*(.*)$", d)
            if m and m.group(1) not in KNOWN_DIRECTIVES:
                agent, d = m.group(1), m.group(2).strip()
            if d and agent in (None, "googlebot", "*"):
                found.append((d, "X-Robots-Tag header"))
    return found


def robots(ctx):
    rules = _robots_directives(ctx)
    names = {d.split(":")[0].strip() for d, _ in rules}
    where = lambda *keys: ", ".join(sorted({w for d, w in rules if d.split(":")[0].strip() in keys}))
    out = []
    if "noindex" in names or "none" in names:
        out.append(issue("robots-noindex", "error", "Search engines are told not to list this page",
                         f"Found “noindex” in the {where('noindex', 'none')}. This page will drop out of search results.",
                         "If this page should appear in search, remove noindex (and none) from the robots meta tag and the X-Robots-Tag header. If it's a private or duplicate page, this is fine."))
    if "nofollow" in names or "none" in names:
        out.append(issue("robots-nofollow", "warning", "Search engines are told not to follow links on this page",
                         f"Found “nofollow” in the {where('nofollow', 'none')}. Pages linked from here get no credit from it and may not be discovered.",
                         "Remove nofollow from the robots meta tag and X-Robots-Tag header unless you really mean it."))
    if "nosnippet" in names:
        out.append(issue("robots-nosnippet", "notice", "Search results won't show a text snippet for this page",
                         f"Found “nosnippet” in the {where('nosnippet')}. Results without a snippet usually get fewer clicks.",
                         "Remove nosnippet unless you need it."))
    if not names & {"noindex", "none"}:
        out.insert(0, issue("robots-ok", "pass", "Search engines may list this page",
                            "No noindex rule in the robots meta tag or X-Robots-Tag header."))
    return out


# ---------- viewport ----------

def viewport(ctx):
    vals = ctx.page.meta("viewport")
    if not vals:
        return [issue("viewport-missing", "error", "Mobile viewport tag is missing",
                      "Phones will show the page zoomed out like a desktop page. Google ranks pages by how they work on phones.",
                      'Add <meta name="viewport" content="width=device-width, initial-scale=1"> in <head>.')]
    v = vals[0].lower().replace(" ", "")
    parts = dict(p.split("=", 1) if "=" in p else (p, "") for p in re.split(r"[,;]", v) if p)
    out = []
    if parts.get("width") != "device-width":
        out.append(issue("viewport-width", "warning", "Viewport tag doesn't use the device width",
                         f"content=“{clip(vals[0])}”. The page may not fit phone screens.",
                         'Use content="width=device-width, initial-scale=1".'))
    try:
        max_scale = float(parts.get("maximum-scale", "10"))
    except ValueError:
        max_scale = 10
    if parts.get("user-scalable") in ("no", "0") or max_scale < 2:
        out.append(issue("viewport-zoom", "notice", "Zooming is blocked on phones",
                         "The viewport tag stops people from zooming in, which makes the page hard to read for many visitors.",
                         "Remove user-scalable=no and maximum-scale from the viewport tag."))
    if not out:
        out.append(issue("viewport-ok", "pass", "Mobile viewport tag is set", f"content=“{clip(vals[0])}”"))
    return out


# ---------- language ----------

def lang(ctx):
    value = ctx.page.lang
    if value is None or not value.strip():
        return [issue("lang-missing", "warning", "Page language isn't set",
                      "The <html> tag has no lang attribute, so search engines and screen readers must guess the language.",
                      'Add the language to the <html> tag, for example <html lang="en"> or <html lang="en-IN">.')]
    if not re.fullmatch(r"[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*", value.strip()):
        return [issue("lang-invalid", "warning", "Page language code isn't valid",
                      f"lang=“{clip(value, 40)}” is not a language code.",
                      'Use a standard code such as "en", "en-GB" or "hi".')]
    return [issue("lang-ok", "pass", "Page language is set", f'lang="{clip(value, 40)}"')]


# ---------- character encoding ----------

def charset(ctx):
    resp = ctx.response
    m = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", resp.header("content-type"), re.I)
    header_cs = m.group(1).lower() if m else ""
    meta_m = fetchmod.META_CHARSET.search(resp.body[:65536])
    meta_cs = meta_m.group(1).decode("ascii", "replace").lower() if meta_m else ""
    norm = lambda c: c.replace("_", "-").replace("utf8", "utf-8")
    if not header_cs and not meta_cs:
        return [issue("charset-missing", "warning", "Character encoding isn't declared",
                      "Neither the server nor the page says which encoding the text uses, so accented letters and symbols may show up garbled.",
                      'Add <meta charset="utf-8"> as the first tag inside <head>.')]
    out = []
    if header_cs and meta_cs and norm(header_cs) != norm(meta_cs):
        out.append(issue("charset-conflict", "warning", "Server and page disagree on the character encoding",
                         f"The server says {header_cs}, the page says {meta_cs}. Browsers follow the server, which may garble text.",
                         "Make both say utf-8: fix the Content-Type header on the server and the <meta charset> tag."))
    used = norm(header_cs or meta_cs)
    if used != "utf-8":
        out.append(issue("charset-not-utf8", "notice", f"Page uses the {used} encoding instead of UTF-8",
                         "UTF-8 is the standard for the web and handles every language.",
                         'Save the page as UTF-8 and declare <meta charset="utf-8">.'))
    if meta_m and not header_cs and meta_m.start() > 1024:
        out.append(issue("charset-late", "notice", "Charset tag comes late in the page",
                         "Browsers only look for it in the first 1024 bytes. After that, they may guess the encoding wrong.",
                         'Move <meta charset="utf-8"> to be the first tag inside <head>.'))
    if not out:
        src = "server header" if header_cs else "<meta charset> tag"
        out.append(issue("charset-ok", "pass", "Character encoding is UTF-8", f"Declared in the {src}."))
    return out


# ---------- favicon ----------

def _reachable(ctx, url):
    """HTTP status for url (HEAD, then a 1-byte GET if HEAD isn't allowed), or None if it couldn't be fetched."""
    r = ctx.fetch(url, method="HEAD", accept="image/*,*/*;q=0.8")
    if r is not None and r.status in (403, 405, 501):
        r = ctx.fetch(url, method="GET", accept="image/*,*/*;q=0.8", max_bytes=1024, extra_headers={"Range": "bytes=0-0"})
    return None if r is None else r.status


def favicon(ctx):
    icons = [l for l in ctx.page.links if {"icon", "apple-touch-icon"} & set((l.get("rel") or "").lower().split())]
    if icons:
        url = icons[0].get("url") or ""
        if url.startswith("data:"):
            return [issue("favicon-ok", "pass", "Favicon is set", "Embedded in the page as data.")]
        if not is_absolute_http(url):
            return [issue("favicon-invalid", "warning", "Favicon link has no valid address",
                          f"href=“{clip(icons[0].get('href') or '', 80)}”.",
                          'Point the <link rel="icon"> tag at your icon file, for example href="/favicon.ico".')]
        status = _reachable(ctx, url)
        if status is None:
            return [issue("favicon-unchecked", "notice", "Favicon couldn't be checked",
                          f"The icon at {clip(url, 120)} didn't answer.", "Open the icon address in a browser to make sure it loads.")]
        if status >= 400:
            return [issue("favicon-broken", "warning", f"Favicon link is broken (status {status})",
                          f"{clip(url, 150)} doesn't load, so browsers and search results show a blank icon.",
                          "Upload the icon file or fix the href in the <link rel=\"icon\"> tag.")]
        return [issue("favicon-ok", "pass", "Favicon is set", clip(url, 150))]
    p = urlsplit(ctx.final_url)
    root_icon = f"{p.scheme}://{p.netloc}/favicon.ico"
    status = _reachable(ctx, root_icon)
    if status is not None and status < 400:
        return [issue("favicon-root", "pass", "Favicon found at /favicon.ico",
                      "There's no <link rel=\"icon\"> tag, but browsers and Google look for /favicon.ico anyway.")]
    return [issue("favicon-missing", "warning", "Favicon is missing",
                  "No <link rel=\"icon\"> tag and no /favicon.ico file. Search results and browser tabs will show a blank icon.",
                  'Add a square icon (at least 48×48 px) and link it with <link rel="icon" href="/favicon.ico"> in <head>.')]


# ---------- Open Graph ----------

OG_TAGS = ("og:title", "og:description", "og:image", "og:url")


def open_graph(ctx):
    found = {t: squash((ctx.page.meta_property(t) or [""])[0]) for t in OG_TAGS + ("og:type",)}
    present = [t for t in OG_TAGS if found[t]]
    if not present:
        return [issue("og-missing", "warning", "Open Graph tags are missing",
                      "When this page is shared on Facebook, LinkedIn, WhatsApp and others, they pick a title and image themselves, often badly.",
                      'Add <meta property="og:title">, og:description, og:image (a full image address, 1200×630 px works well) and og:url in <head>.')]
    out = []
    missing = [t for t in OG_TAGS if not found[t]]
    if missing:
        out.append(issue("og-incomplete", "notice", f"Open Graph is missing {', '.join(missing)}",
                         f"Found {', '.join(present)}.",
                         f"Add {' and '.join(missing)} so shared links show a full preview."))
    img = found["og:image"]
    if img and not is_absolute_http(img):
        out.append(issue("og-image-relative", "warning", "og:image isn't a full address",
                         f"“{clip(img, 100)}”. Most sites that read Open Graph need the full address and will show no image.",
                         "Use the full image address, starting with https://."))
    if not out:
        out.append(issue("og-ok", "pass", "Open Graph tags are complete", ", ".join(present)))
    return out


# ---------- Twitter / X card ----------

def twitter(ctx):
    card = squash((ctx.page.meta_property("twitter:card") or [""])[0]).lower()
    if not card:
        return [issue("twitter-missing", "notice", "Twitter (X) card tag is missing",
                      "X uses Open Graph for the title and image, but without twitter:card links show as a small preview.",
                      'Add <meta name="twitter:card" content="summary_large_image"> in <head>.')]
    if card not in ("summary", "summary_large_image", "app", "player"):
        return [issue("twitter-invalid", "warning", "Twitter (X) card type isn't valid",
                      f"twitter:card is “{clip(card, 40)}”.",
                      'Use content="summary_large_image" (big image) or "summary" (small image).')]
    return [issue("twitter-ok", "pass", "Twitter (X) card tag is set", f"twitter:card = {card}")]


# ---------- JSON-LD structured data ----------

def _types(node, found):
    if isinstance(node, list):
        for n in node:
            _types(n, found)
    elif isinstance(node, dict):
        t = node.get("@type")
        for x in (t if isinstance(t, list) else [t]):
            if isinstance(x, str) and x not in found:
                found.append(x)
        if "@graph" in node:
            _types(node["@graph"], found)


def json_ld(ctx):
    blocks = ctx.page.json_ld
    if not blocks:
        return [issue("jsonld-missing", "notice", "No structured data (JSON-LD)",
                      "Structured data tells search engines what the page is (a business, article, product, event…) and can earn rich results such as stars, prices or FAQs.",
                      "Add a <script type=\"application/ld+json\"> block with schema.org data that matches the page, for example Organization, Article, Product or LocalBusiness.")]
    out, types, valid = [], [], 0
    for i, b in enumerate(blocks, 1):
        label = f"Structured data block {i}" if len(blocks) > 1 else "Structured data"
        text = (b.get("text") or "").strip()
        if text.startswith("<!--"):
            text = text[4:]
        if text.endswith("-->"):
            text = text[:-3]
        text = text.strip()
        if not text:
            out.append(issue(f"jsonld-empty-{i}", "warning", f"{label} is empty",
                             "The JSON-LD script tag has nothing in it.", "Fill it with schema.org data, or remove the empty tag."))
            continue
        try:
            data = json.loads(text)
        except ValueError as e:
            msg = getattr(e, "msg", "invalid JSON")
            pos = f" at line {e.lineno}, column {e.colno}" if hasattr(e, "lineno") else ""
            out.append(issue(f"jsonld-invalid-{i}", "error", f"{label} isn't valid JSON",
                             f"{msg}{pos}. Search engines ignore the whole block.",
                             "Fix the JSON (a missing comma or quote is the usual cause). Paste it into any JSON validator to find the spot."))
            continue
        valid += 1
        nodes = data if isinstance(data, list) else [data]
        if not any(isinstance(n, dict) and "@context" in n for n in nodes):
            out.append(issue(f"jsonld-context-{i}", "warning", f"{label} has no @context",
                             "Without \"@context\": \"https://schema.org\", search engines don't know what vocabulary the data uses.",
                             'Add "@context": "https://schema.org" at the top level of the block.'))
        _types(data, types)
    if valid:
        names = ", ".join(types[:8]) + ("…" if len(types) > 8 else "") if types else "no @type given"
        out.append(issue("jsonld-ok", "pass", f"Structured data found ({valid} block{'s' if valid > 1 else ''})", names))
    return out
