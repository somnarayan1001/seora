"""Technical: status code, HTTPS, mixed content, redirects, compression, caching, robots.txt and sitemap.

Side requests (the http:// address, Brotli, a few files for caching, robots.txt, the sitemap) run
side by side through ctx.try_fetch, so they share the report's connections and safety rules.
"""

import re
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import config

from ..robots import MAX_BYTES as ROBOTS_MAX_BYTES
from ..robots import Robots
from ..urls import comparable, is_absolute_http
from ._fetchmany import fetch_many
from ._issue import clip, issue

GROUP = "Technical"

EXAMPLES = 5
COMPRESS_MIN_BYTES = 1400        # smaller pages fit in one network packet anyway
CACHE_MIN_SECONDS = 7 * 86400    # files should be cached for at least a week
CACHE_SAMPLES = 6                # CSS, JS and image files checked for caching
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
SITEMAP_MAX_URLS = 50_000


def run(ctx):
    p = urlsplit(ctx.final_url)
    root = f"{p.scheme}://{p.netloc}"
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {name: pool.submit(fn, ctx, root) for name, fn in (
            ("http", http_version), ("brotli", brotli), ("cache", caching), ("robots", robots_and_sitemap))}
        side = {name: f.result() for name, f in jobs.items()}
    return (status(ctx) + https(ctx) + side["http"] + mixed_content(ctx) + redirect_chain(ctx)
            + compression(ctx) + side["brotli"] + side["cache"] + side["robots"])


def _examples(items, label=lambda x: x):
    names = list(dict.fromkeys(label(i) for i in items))
    shown = ", ".join(names[:EXAMPLES])
    return shown + (f" and {len(names) - EXAMPLES} more" if len(names) > EXAMPLES else "")


def _kb(n):
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def _name(url):
    path = urlsplit(url).path.rstrip("/")
    return clip(path.rsplit("/", 1)[-1] or urlsplit(url).hostname or url, 40)


# ---------- status ----------

def status(ctx):
    s = ctx.response.status
    reason = ctx.response.reason
    if s == 200:
        return [issue("status-ok", "pass", "Page answers with status 200 (OK)")]
    if s < 300:
        return [issue("status-unusual", "notice", f"Page answers with status {s} {reason}".strip(),
                      "Search engines expect 200 for a normal page.",
                      "Make the server answer this page with status 200.")]
    return [issue("status-error", "error", f"Page answers with status {s} {reason}".strip(),
                  "Search engines don't list pages that answer with an error, and drop them if they were listed before.",
                  "If the page should exist, fix it so it answers with 200. If it moved, redirect its old address to the new one with a 301.")]


# ---------- HTTPS ----------

def https(ctx):
    if urlsplit(ctx.final_url).scheme != "https":
        return [issue("https-missing", "error", "Page isn't served over HTTPS",
                      "Browsers mark http:// pages as “Not secure”, and Google prefers secure pages in search results.",
                      "Get a free certificate (for example from Let's Encrypt, or your host's control panel), serve the site on https://, and redirect every http:// address to it.")]
    out = [issue("https-ok", "pass", "Page is served over HTTPS")]
    hsts = ctx.response.header("strict-transport-security")
    if not hsts:
        out.append(issue("hsts-missing", "notice", "HSTS isn't set",
                         "The Strict-Transport-Security header tells browsers to always use https:// for this site, even if someone types http://.",
                         "Once the whole site works on HTTPS, send the header Strict-Transport-Security: max-age=31536000."))
    return out


def http_version(ctx, root):
    """Does the http:// address of this page redirect to https://?"""
    p = urlsplit(ctx.final_url)
    if p.scheme != "https" or p.port not in (None, 443):
        return []
    http_url = "http://" + p.hostname + (p.path or "/") + (f"?{p.query}" if p.query else "")
    r, code = ctx.try_fetch(http_url, method="HEAD", timeout=config.ASSET_TIMEOUT)
    if r is not None and r.status in (403, 405, 501):
        r, code = ctx.try_fetch(http_url, method="GET", max_bytes=2048, timeout=config.ASSET_TIMEOUT)
    if r is None:
        return []  # nothing listens on http://, which is fine
    if urlsplit(r.url).scheme == "https":
        return [issue("http-redirect-ok", "pass", "The http:// address redirects to https://")]
    if r.status < 400:
        return [issue("http-no-redirect", "warning", "The http:// address doesn't redirect to https://",
                      f"{clip(http_url, 100)} answers with {r.status} on its own, so the same page exists twice and visitors may land on the insecure copy.",
                      "Redirect every http:// address to the same address on https:// with a 301.")]
    return []


def mixed_content(ctx):
    if urlsplit(ctx.final_url).scheme != "https":
        return []
    page = ctx.page
    active, passive = [], []
    insecure = lambda u: (u or "").lower().startswith("http://")
    for s in page.scripts:
        if insecure(s.get("url")):
            active.append(s["url"])
    for l in page.links:
        rels = set((l.get("rel") or "").lower().split())
        if insecure(l.get("url")):
            if rels & {"stylesheet", "preload", "modulepreload", "manifest"}:
                active.append(l["url"])
            elif rels & {"icon", "apple-touch-icon"}:
                passive.append(l["url"])
    for m in page.media:
        target = active if m["tag"] in ("iframe", "embed", "object") else passive
        for u in [m.get("url"), m.get("poster")] + _srcset_urls(m.get("srcset")):
            if insecure(u):
                target.append(u)
    for i in page.images:
        for u in [i.get("url")] + _srcset_urls(i.get("srcset")):
            if insecure(u):
                passive.append(u)
    out = []
    if active:
        out.append(issue("mixed-active", "error", f"{len(set(active))} script, style or frame file{'s' if len(set(active)) != 1 else ''} load over insecure http://",
                         _examples(active, _name) + ". Browsers block these on a secure page, so parts of the page may break, and they show a security warning.",
                         "Change these addresses to https:// (or load the files from your own site)."))
    if passive:
        out.append(issue("mixed-passive", "warning", f"{len(set(passive))} image or media file{'s' if len(set(passive)) != 1 else ''} load over insecure http://",
                         _examples(passive, _name) + ". Browsers try https:// instead or show the page as not fully secure.",
                         "Change these addresses to https://."))
    if not out:
        out.append(issue("mixed-ok", "pass", "No insecure (http://) files on this secure page"))
    return out


def _srcset_urls(srcset):
    if not srcset or "data:" in srcset:
        return []
    return [part.strip().split()[0] for part in srcset.split(",") if part.strip()]


# ---------- redirects ----------

def redirect_chain(ctx):
    chain = ctx.response.chain
    if not chain:
        return [issue("redirects-none", "pass", "No redirects to reach this page")]
    steps = " → ".join(f"{clip(h.url, 60)} ({h.status})" for h in chain) + f" → {clip(ctx.final_url, 60)}"
    out = []
    if len(chain) > 1:
        out.append(issue("redirects-chain", "warning", f"Page is reached through {len(chain)} redirects",
                         steps + ". Each step adds a delay, and search engines may stop following long chains.",
                         "Redirect old addresses straight to the final address in one step, and update links to point to it."))
    temporary = [h for h in chain if h.status in (302, 303, 307)]
    if temporary:
        out.append(issue("redirects-temporary", "notice", f"Redirect is temporary ({temporary[0].status})",
                         steps + ". With a temporary redirect, search engines may keep listing the old address.",
                         "If the move is permanent, use a 301 (or 308) redirect so search engines switch to the new address."))
    if not out:
        out.append(issue("redirects-one", "pass", "One permanent redirect to reach this page", steps))
    return out


# ---------- compression ----------

def compression(ctx):
    r = ctx.response
    size = len(r.body)
    enc = r.content_encoding
    if enc in ("gzip", "x-gzip", "deflate", "br", "zstd"):
        return [issue("compression-ok", "pass", f"Page is compressed ({enc})",
                      f"{_kb(r.transfer_bytes)} sent for {_kb(size)} of HTML.")]
    if size < COMPRESS_MIN_BYTES:
        return [issue("compression-small", "pass", "Page is too small to need compression")]
    return [issue("compression-missing", "warning", "Page isn't compressed",
                  f"{_kb(size)} of HTML is sent as it is. Compression usually makes HTML 70–80% smaller, so pages load faster.",
                  "Turn on gzip or Brotli compression on the server or CDN. It's usually a single setting.")]


def brotli(ctx, root):
    if ctx.response.content_encoding not in ("gzip", "x-gzip", "deflate"):
        return []  # either no compression (reported above) or already Brotli
    r = ctx.fetch(ctx.final_url, method="GET", max_bytes=2048, timeout=config.ASSET_TIMEOUT,
                  extra_headers={"Accept-Encoding": "br, gzip"})
    if r is None:
        return []
    if r.content_encoding == "br":
        return [issue("brotli-ok", "pass", "Brotli compression is available", "Browsers that ask for Brotli get it.")]
    return [issue("brotli-missing", "notice", "Brotli compression isn't used",
                  "The page uses gzip, which is good. Brotli usually makes HTML another 15–20% smaller, and every current browser supports it.",
                  "Turn on Brotli on the server or CDN, keeping gzip for older clients.")]


# ---------- caching ----------

def _cache_seconds(resp):
    """How long a browser may reuse this file without asking again, in seconds (0 when not cached)."""
    cc = resp.header("cache-control").lower()
    if "no-store" in cc or "no-cache" in cc:
        return 0
    m = re.search(r"(?:s-maxage|max-age)\s*=\s*(\d+)", cc)
    if m:
        return int(m.group(1))
    if "immutable" in cc:
        return CACHE_MIN_SECONDS
    exp, date = resp.header("expires"), resp.header("date")
    try:
        return max(0, int((parsedate_to_datetime(exp) - parsedate_to_datetime(date)).total_seconds()))
    except (TypeError, ValueError, IndexError):
        return 0


def _duration(seconds):
    if seconds <= 0:
        return "not cached"
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= n:
            v = seconds // n
            return f"{v} {unit}{'s' if v != 1 else ''}"
    return f"{seconds} seconds"


def _site(host):
    """Rough 'same company' test: the last two parts of a host name (three for names like bbc.co.uk)."""
    parts = host.lower().rstrip(".").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "edu"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _cache_probe(ctx, url):
    # A 1-byte ranged GET: only the headers matter, and some file servers stall on HEAD
    r = ctx.fetch(url, method="GET", accept="*/*", max_bytes=1024, timeout=config.ASSET_TIMEOUT,
                  extra_headers={"Range": "bytes=0-0"})
    if r is None or r.status >= 400:
        return None
    return _cache_seconds(r)


def caching(ctx, root):
    out = []
    r = ctx.response
    if not (r.header("cache-control") or r.header("etag") or r.header("last-modified") or r.header("expires")):
        out.append(issue("cache-page-none", "notice", "Page has no caching headers",
                         "Without Cache-Control, ETag or Last-Modified, browsers must download the whole page again on every visit.",
                         "Send an ETag or Last-Modified header with the page, so a browser can ask “has it changed?” and get a quick “no”."))
    page = ctx.page
    files = [l["url"] for l in page.links if "stylesheet" in (l.get("rel") or "").lower().split() and is_absolute_http(l.get("url") or "")]
    files += [s["url"] for s in page.scripts if s.get("url") and is_absolute_http(s["url"])]
    files += [i["url"] for i in page.images if is_absolute_http(i.get("url") or "")]
    files = list(dict.fromkeys(files))[:CACHE_SAMPLES]
    if not files:
        return out
    results, _, _ = fetch_many(ctx, files, _cache_probe, CACHE_SAMPLES)
    measured = {u: s for u, s in results.items() if s is not None}
    if not measured:
        return out
    short = sorted((u for u, s in measured.items() if s < CACHE_MIN_SECONDS), key=lambda u: measured[u])
    site = _site(urlsplit(ctx.final_url).hostname or "")
    own = [u for u in short if _site(urlsplit(u).hostname or "") == site]
    others = [u for u in short if u not in own]
    label = lambda u: f"{_name(u) if u in own else (urlsplit(u).hostname or '') + '/' + _name(u)} ({_duration(measured[u])})"
    if own:
        out.append(issue("cache-files-short", "warning",
                         ("The one file checked is cached for less than a week" if len(measured) == 1 else
                          f"{len(own)} of {len(measured)} files checked {'is' if len(own) == 1 else 'are'} cached for less than a week"),
                         _examples(own, label) + ". Returning visitors download these files again, which slows the page down."
                         + (f" Also {len(others)} from other sites: {_examples(others, label)}." if others else ""),
                         "Give CSS, JavaScript and image files a long cache time, e.g. Cache-Control: max-age=31536000, immutable. "
                         "Change a file's name or add a version (style.css?v=2) when it changes, so visitors still get updates."))
    elif others:
        out.append(issue("cache-files-third-party", "notice",
                         f"{len(others)} file{'s' if len(others) != 1 else ''} from other sites {'are' if len(others) != 1 else 'is'} cached for less than a week",
                         _examples(others, label) + ". These come from other companies (analytics, widgets, CDNs), so their caching is set by them. "
                         "Your own files checked are cached well.",
                         "Nothing to fix on your server. If one of these slows the page, consider whether you still need it."))
    else:
        out.append(issue("cache-files-ok", "pass", "Files are cached for a long time",
                         f"{len(measured)} CSS, JavaScript and image files checked; all are cached for at least a week."))
    return out


# ---------- robots.txt and sitemap ----------

def robots_and_sitemap(ctx, root):
    out, robots = robots_txt(ctx, root)
    return out + sitemap(ctx, root, robots)


def robots_txt(ctx, root):
    url = root + "/robots.txt"
    r, code = ctx.try_fetch(url, method="GET", accept="text/plain,*/*;q=0.5", max_bytes=ROBOTS_MAX_BYTES + 1,
                            timeout=config.ASSET_TIMEOUT)
    if r is None:
        return [issue("robotstxt-unchecked", "notice", "robots.txt couldn't be checked",
                      f"{url} didn't answer.", "Open /robots.txt in a browser to make sure it loads.")], None
    if r.status >= 500:
        return [issue("robotstxt-server-error", "error", f"robots.txt answers with a server error ({r.status})",
                      "When robots.txt fails like this, Google stops crawling the whole site until it works again.",
                      "Fix the server so /robots.txt answers with 200 (or with 404 if you don't want one).")], None
    if r.status >= 400:
        return [issue("robotstxt-missing", "notice", "No robots.txt",
                      f"{url} answers with {r.status}. Search engines may then crawl everything, which is fine for most sites, "
                      "but robots.txt is also the usual place to point them to your sitemap.",
                      f"Add a /robots.txt file. A simple one has three lines: “User-agent: *”, “Allow: /” and “Sitemap: {root}/sitemap.xml”.")], None
    head = r.body[:300].lstrip().lower()
    if r.content_type == "text/html" or head.startswith((b"<!doctype", b"<html")):
        return [issue("robotstxt-html", "warning", "robots.txt is a web page, not a text file",
                      "The address answers with HTML (often the site's normal page shown for any address), so search engines can't read any rules or sitemap from it.",
                      "Serve a plain-text /robots.txt file with Content-Type: text/plain.")], None
    text = r.body.decode("utf-8", errors="replace").lstrip("﻿")
    robots = Robots(text[:ROBOTS_MAX_BYTES])
    out = []
    allowed, rule = robots.allowed(ctx.final_url, "googlebot")
    if not allowed:
        out.append(issue("robotstxt-blocks-page", "error", "robots.txt blocks this page",
                         f"The rule “{clip(rule, 80)}” stops search engines from crawling this page, so its content won't be read and it rarely ranks.",
                         "If this page should appear in search, change or remove that rule in robots.txt."))
    if len(r.body) > ROBOTS_MAX_BYTES:
        out.append(issue("robotstxt-too-big", "notice", "robots.txt is larger than 500 KB",
                         "Google ignores everything after the first 500 KB.", "Shorten robots.txt, for example by combining rules with *."))
    if robots.problems or robots.orphan_rules:
        n = len(robots.problems) + robots.orphan_rules
        shown = "; ".join(f"line {ln}: “{clip(t, 40)}”" for ln, t in robots.problems[:3])
        extra = f" {robots.orphan_rules} Allow/Disallow line{'s' if robots.orphan_rules != 1 else ''} come before any User-agent line and are ignored." if robots.orphan_rules else ""
        out.append(issue("robotstxt-invalid-lines", "warning", f"{n} line{'s' if n != 1 else ''} in robots.txt can't be understood",
                         (shown + "." if shown else "") + extra + " Search engines skip lines they don't understand, so these rules may not do what you expect.",
                         "Use only User-agent, Allow, Disallow and Sitemap lines, written as “Field: value”, with rules under a User-agent line."))
    if allowed:
        groups = len(robots.groups)
        out.insert(0, issue("robotstxt-ok", "pass", "robots.txt allows this page",
                            f"{groups} group{'s' if groups != 1 else ''} of rules; {len(robots.sitemaps)} sitemap{'s' if len(robots.sitemaps) != 1 else ''} listed."))
    return out, robots


def sitemap(ctx, root, robots):
    if robots is not None and robots.sitemaps:
        candidates, listed = [urljoin(root + "/", s) for s in robots.sitemaps], True
    else:
        candidates, listed = [root + "/sitemap.xml", root + "/sitemap_index.xml"], False
    url = r = None
    for url in candidates[:2]:
        if not is_absolute_http(url):
            continue
        r, _ = ctx.try_fetch(url, method="GET", accept="application/xml,text/xml;q=0.9,*/*;q=0.5",
                             max_bytes=config.MAX_PAGE_BYTES, timeout=config.ASSET_TIMEOUT * 2)
        if r is not None and r.status < 400:
            break
    if r is None:
        return [issue("sitemap-unchecked", "notice", "Sitemap couldn't be checked",
                      f"{clip(url or '', 100)} didn't answer.", "Open the sitemap in a browser to make sure it loads.")]
    if r.status >= 400:
        if listed and r.status in (401, 403, 429):
            return [issue("sitemap-refused", "notice", f"Sitemap refused Seora's check ({r.status})",
                          f"{clip(r.url, 100)} is named in robots.txt but turned Seora away. Sites with bot protection do this; "
                          "search engines may still be let in.",
                          "Open the sitemap in a browser, and check in Google Search Console that Google can read it.")]
        if listed:
            return [issue("sitemap-broken", "error", f"Sitemap listed in robots.txt doesn't load ({r.status})",
                          f"{clip(candidates[0], 100)} is named in robots.txt but answers with an error, so search engines can't use it.",
                          "Fix the sitemap address in robots.txt, or make the sitemap available at that address.")]
        return [issue("sitemap-missing", "warning", "No sitemap found",
                      "robots.txt doesn't name a sitemap, and there is none at /sitemap.xml. A sitemap helps search engines find every page, especially new ones.",
                      f"Create an XML sitemap listing your pages (most site builders and CMS plugins make one), and add “Sitemap: {root}/sitemap.xml” to robots.txt.")]
    return _sitemap_content(ctx, r, listed, len(candidates) if listed else 1)


def _sitemap_content(ctx, r, listed, how_many):
    where = clip(r.url, 100)
    if how_many > 1:
        where += f" (the first of {how_many} sitemaps in robots.txt)"
    body = r.body
    if body[:2] == b"\x1f\x8b":  # a .xml.gz file served as it is
        try:
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, config.MAX_PAGE_BYTES)
        except zlib.error:
            return [issue("sitemap-invalid", "error", "Sitemap can't be read", f"{where} is a damaged compressed file.",
                          "Regenerate the sitemap file.")]
    head = body[:300].lstrip().lower()
    if r.content_type == "text/html" or head.startswith((b"<!doctype html", b"<html")):
        return [issue("sitemap-html", "error", "Sitemap address returns a web page",
                      f"{where} answers with an HTML page instead of an XML sitemap (often the site's normal page shown for any address).",
                      "Serve a real XML sitemap at this address, or point robots.txt to the right one.")]
    if b"<!doctype" in body[:2000].lower() or b"<!entity" in body.lower():
        return [issue("sitemap-invalid", "error", "Sitemap isn't a valid sitemap",
                      f"{where} uses a DOCTYPE or entities, which sitemaps don't allow (Seora doesn't read them, for safety).",
                      "Generate the sitemap without a DOCTYPE, following sitemaps.org.")]
    try:
        tree = ET.fromstring(body)
    except ET.ParseError as e:
        return [issue("sitemap-invalid", "error", "Sitemap isn't valid XML",
                      f"{where}: {e}. Search engines may ignore the whole file." + (" (Only the first part was read, because the file is very large.)" if r.truncated else ""),
                      "Fix the XML (an unescaped & in an address is the usual cause: write it as &amp;), or regenerate the sitemap.")]
    ns, _, kind = tree.tag.rpartition("}")
    ns = ns.lstrip("{")
    out = []
    if kind not in ("urlset", "sitemapindex"):
        return [issue("sitemap-invalid", "error", "Sitemap has the wrong format",
                      f"{where} is XML, but its main element is <{clip(kind, 30)}> instead of <urlset> or <sitemapindex>.",
                      "Generate the sitemap in the sitemaps.org format.")]
    if ns != SITEMAP_NS:
        out.append(issue("sitemap-namespace", "warning", "Sitemap uses the wrong namespace",
                         f"Expected xmlns=\"{SITEMAP_NS}\". Some search engines reject sitemaps without it.",
                         f'Add xmlns="{SITEMAP_NS}" to the <{kind}> element.'))
    q = lambda tag: f"{{{ns}}}{tag}" if ns else tag
    if kind == "sitemapindex":
        n = len(tree.findall(q("sitemap")))
        if not n:
            out.append(issue("sitemap-empty", "warning", "Sitemap index is empty", f"{where} lists no sitemaps.",
                             "Add the sitemaps of your site to the index, or remove the empty index."))
        else:
            out.insert(0, issue("sitemap-ok", "pass", f"Sitemap index found with {n} sitemap{'s' if n != 1 else ''}",
                                f"{where}" + (" (from robots.txt)" if listed and how_many == 1 else "") + ". The sitemaps inside it weren't opened."))
        return out
    locs = [(e.text or "").strip() for e in tree.iter(q("loc"))]
    if not locs:
        out.append(issue("sitemap-empty", "warning", "Sitemap is empty", f"{where} lists no pages.",
                         "Add your pages to the sitemap, or check the tool that generates it."))
        return out
    if len(locs) > SITEMAP_MAX_URLS:
        out.append(issue("sitemap-too-many", "error", f"Sitemap has {len(locs):,} addresses",
                         f"The limit is {SITEMAP_MAX_URLS:,} per file; search engines may ignore the rest.",
                         "Split it into several sitemaps and list them in a sitemap index."))
    bad = [l for l in locs if not is_absolute_http(l)]
    if bad:
        out.append(issue("sitemap-bad-urls", "warning", f"{len(bad)} sitemap address{'es' if len(bad) != 1 else ''} aren't full web addresses",
                         _examples(bad, lambda x: f"“{clip(x, 50)}”") + ". Every <loc> must be a full address starting with https://.",
                         "Use full addresses in the sitemap."))
    out.insert(0, issue("sitemap-ok", "pass", f"Sitemap found with {len(locs):,} address{'es' if len(locs) != 1 else ''}",
                        where + (" (from robots.txt)" if listed and how_many == 1 else "") + (". Only the first part was read, because the file is very large." if r.truncated else "")))
    # Only fair with a single sitemap: with several, this page may be in another one
    if how_many == 1 and not r.truncated and ctx.response.status == 200:
        here = comparable(ctx.final_url)
        if here not in {comparable(l) for l in locs}:
            out.append(issue("sitemap-page-missing", "notice", "This page isn't in the sitemap",
                             f"{where} doesn't list {clip(ctx.final_url, 100)}. Pages in the sitemap are found and re-checked sooner.",
                             "Add this page's address to the sitemap, unless it shouldn't appear in search."))
    return out
