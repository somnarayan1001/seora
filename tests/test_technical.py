"""Technical check group on hand-made pages and a fake web (no network). Run with:  python -m unittest discover -s tests"""

import gzip
import unittest

from seora.audit import Context
from seora.checks import technical
from seora.fetch import Hop, Response
from seora.parse import parse

URL = "https://example.com/page"
S = "https://example.com"
GOOD_SITEMAP = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b"<url><loc>https://example.com/page</loc></url><url><loc>https://example.com/</loc></url></urlset>")


def site(**over):
    """Fake answers by URL: (status, body, headers). Anything not listed answers 404."""
    files = {
        "http://example.com/page": ("redirect", URL),
        S + "/robots.txt": (200, b"User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n", [("Content-Type", "text/plain")]),
        S + "/sitemap.xml": (200, GOOD_SITEMAP, [("Content-Type", "application/xml")]),
        S + "/app.js": (200, b"", [("Cache-Control", "public, max-age=31536000, immutable")]),
        S + "/style.css": (200, b"", [("Cache-Control", "max-age=2592000")]),
        "br": "br",
    }
    files.update(over)
    calls = []

    def try_fetch(url, method="GET", extra_headers=None, **kw):
        calls.append((method, url))
        if url == URL and extra_headers and "br" in extra_headers.get("Accept-Encoding", ""):
            return Response(url=URL, status=200, reason="", headers=[("Content-Encoding", files["br"])], body=b"",
                            content_encoding=files["br"]), None
        spec = files.get(url, (404, b"", []))
        chain = []
        if spec[0] == "redirect":
            chain, url = [Hop(url, 301, spec[1])], spec[1]
            spec = (200, b"", [])
        if spec[0] == "fail":
            return None, spec[1]
        return Response(url=url, status=spec[0], reason="", headers=spec[2], body=spec[1], chain=chain), None

    try_fetch.calls = calls
    return try_fetch


def run(body="<p>Hello</p>", headers=None, url=URL, chain=None, encoding="gzip", fetcher=None, status=200):
    html = (f'<!doctype html><html><head><title>T</title><link rel="stylesheet" href="/style.css">'
            f'<script src="/app.js"></script></head><body>{body}</body></html>')
    hdrs = headers if headers is not None else [("Strict-Transport-Security", "max-age=31536000"), ("ETag", '"1"')]
    resp = Response(url=url, status=status, reason="OK", headers=hdrs, body=html.encode("utf-8") + b" " * 3000,
                    content_encoding=encoding, transfer_bytes=900, chain=chain or [])
    ctx = Context(requested_url=url, final_url=url, response=resp, page=parse(html, url), html=html,
                  charset="utf-8", charset_source="header")
    ctx.try_fetch = fetcher or site()
    return {i["id"]: i for i in technical.run(ctx)}


class TechnicalChecks(unittest.TestCase):
    def test_good_site(self):
        res = run()
        problems = {k: v["title"] for k, v in res.items() if v["severity"] != "pass"}
        self.assertEqual(problems, {})
        self.assertIn("Sitemap found with 2 addresses", res["sitemap-ok"]["title"])

    def test_status_and_http(self):
        self.assertEqual(run(status=404)["status-error"]["severity"], "error")
        res = run(url="http://example.com/page")
        self.assertEqual(res["https-missing"]["severity"], "error")
        self.assertNotIn("mixed-ok", res)

    def test_http_address_not_redirecting(self):
        res = run(fetcher=site(**{"http://example.com/page": (200, b"", [])}))
        self.assertEqual(res["http-no-redirect"]["severity"], "warning")

    def test_hsts_missing(self):
        self.assertIn("hsts-missing", run(headers=[("ETag", '"1"')]))

    def test_mixed_content(self):
        body = ('<img src="http://cdn.example.com/a.jpg"><iframe src="http://video.example.org/e"></iframe>'
                '<script src="http://cdn.example.com/x.js"></script>'
                '<img src="/ok.jpg" srcset="http://cdn.example.com/b.jpg 2x"><a href="http://other.org/">fine</a>')
        res = run(body)
        self.assertEqual(res["mixed-active"]["title"], "2 script, style or frame files load over insecure http://")
        self.assertEqual(res["mixed-passive"]["title"], "2 image or media files load over insecure http://")

    def test_redirect_chain(self):
        chain = [Hop("http://example.com/old", 301, "https://example.com/old"), Hop("https://example.com/old", 302, URL)]
        res = run(chain=chain)
        self.assertEqual(res["redirects-chain"]["title"], "Page is reached through 2 redirects")
        self.assertIn("redirects-temporary", res)
        self.assertIn("redirects-one", run(chain=[Hop("https://example.com/old", 301, URL)]))

    def test_compression(self):
        self.assertEqual(run(encoding="")["compression-missing"]["severity"], "warning")
        self.assertIn("brotli-missing", run(fetcher=site(br="gzip")))

    def test_caching(self):
        res = run(fetcher=site(**{S + "/app.js": (200, b"", [("Cache-Control", "no-cache")])}))
        self.assertIn("app.js (not cached)", res["cache-files-short"]["detail"])
        res = run(headers=[("Strict-Transport-Security", "max-age=1")])
        self.assertIn("cache-page-none", res)

    def test_robots_cases(self):
        self.assertIn("robotstxt-missing", run(fetcher=site(**{S + "/robots.txt": (404, b"", [])})))
        self.assertEqual(run(fetcher=site(**{S + "/robots.txt": (503, b"", [])}))["robotstxt-server-error"]["severity"], "error")
        html = run(fetcher=site(**{S + "/robots.txt": (200, b"<!DOCTYPE html><html>", [("Content-Type", "text/html")])}))
        self.assertIn("robotstxt-html", html)
        blocked = run(fetcher=site(**{S + "/robots.txt": (200, b"User-agent: *\nDisallow: /pa\n", [])}))
        self.assertIn("Disallow: /pa", blocked["robotstxt-blocks-page"]["detail"])
        odd = run(fetcher=site(**{S + "/robots.txt": (200, b"User-agent: *\nDisalow /x\nAllow: /\n", [])}))
        self.assertIn("robotstxt-invalid-lines", odd)

    def test_sitemap_cases(self):
        no_robots = {S + "/robots.txt": (404, b"", [])}
        self.assertIn("sitemap-missing", run(fetcher=site(**no_robots, **{S + "/sitemap.xml": (404, b"", [])})))
        self.assertIn("sitemap-broken", run(fetcher=site(**{S + "/sitemap.xml": (404, b"", [])})))
        bad_xml = run(fetcher=site(**{S + "/sitemap.xml": (200, b"<urlset><url><loc>a&b</loc></url></urlset>", [])}))
        self.assertIn("sitemap-invalid", bad_xml)
        html = run(fetcher=site(**{S + "/sitemap.xml": (200, b"<!doctype html><html></html>", [("Content-Type", "text/html")])}))
        self.assertIn("sitemap-html", html)
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset>&a;</urlset>'
        self.assertIn("sitemap-invalid", run(fetcher=site(**{S + "/sitemap.xml": (200, bomb, [])})))
        other = GOOD_SITEMAP.replace(b"https://example.com/page", b"https://example.com/else")
        self.assertIn("sitemap-page-missing", run(fetcher=site(**{S + "/sitemap.xml": (200, other, [])})))

    def test_sitemap_index_gzip_and_fallback(self):
        index = (b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://example.com/a.xml</loc></sitemap>'
                 b"<sitemap><loc>https://example.com/b.xml</loc></sitemap></sitemapindex>")
        res = run(fetcher=site(**{S + "/sitemap.xml": (200, index, [])}))
        self.assertEqual(res["sitemap-ok"]["title"], "Sitemap index found with 2 sitemaps")
        res = run(fetcher=site(**{S + "/sitemap.xml": (200, gzip.compress(GOOD_SITEMAP), [])}))
        self.assertIn("sitemap-ok", res)
        res = run(fetcher=site(**{S + "/robots.txt": (404, b"", []), S + "/sitemap.xml": (404, b"", []),
                                  S + "/sitemap_index.xml": (200, index, [])}))
        self.assertIn("sitemap-ok", res)

    def test_several_sitemaps_and_refused(self):
        robots = (200, b"User-agent: *\nAllow: /\nSitemap: https://example.com/a.xml\nSitemap: https://example.com/b.xml\n", [])
        other = GOOD_SITEMAP.replace(b"https://example.com/page", b"https://example.com/else")
        res = run(fetcher=site(**{S + "/robots.txt": robots, S + "/a.xml": (200, other, [])}))
        self.assertIn("the first of 2 sitemaps", res["sitemap-ok"]["detail"])
        self.assertNotIn("sitemap-page-missing", res)      # this page may be in b.xml
        res = run(fetcher=site(**{S + "/sitemap.xml": (403, b"", [])}))
        self.assertEqual(res["sitemap-refused"]["severity"], "notice")
        # guessed /sitemap.xml (no robots.txt) still counts as a single sitemap
        res = run(fetcher=site(**{S + "/robots.txt": (404, b"", []), S + "/sitemap.xml": (200, other, [])}))
        self.assertIn("sitemap-page-missing", res)

    def test_third_party_files_cached_briefly_is_a_notice(self):
        body = '<script src="https://www.googletagmanager.com/gtag/js?id=1"></script>'
        res = run(body, fetcher=site(**{"https://www.googletagmanager.com/gtag/js?id=1": (200, b"", [("Cache-Control", "max-age=900")])}))
        self.assertEqual(res["cache-files-third-party"]["severity"], "notice")
        self.assertIn("www.googletagmanager.com/js (15 minutes)", res["cache-files-third-party"]["detail"])
        self.assertNotIn("cache-files-short", res)

    def test_unreachable_side_requests(self):
        res = run(fetcher=lambda url, **kw: (None, "timeout"))
        self.assertIn("robotstxt-unchecked", res)
        self.assertNotIn("http-no-redirect", res)


if __name__ == "__main__":
    unittest.main()
