"""Meta check group on hand-made pages (no network). Run with:  python -m unittest discover -s tests"""

import unittest

from seora.audit import Context
from seora.checks import meta
from seora.fetch import Response
from seora.parse import parse

URL = "https://shop.example.com/shoes"


def run(html, headers=None, fetch_status=200):
    headers = headers if headers is not None else [("Content-Type", "text/html; charset=utf-8")]
    resp = Response(url=URL, status=200, reason="OK", headers=headers, body=html.encode("utf-8"))
    ctx = Context(requested_url=URL, final_url=URL, response=resp, page=parse(html, URL), html=html,
                  charset="utf-8", charset_source="header")
    # No network in tests: every extra fetch (favicon) answers with fetch_status
    ctx.fetch = lambda url, **kw: Response(url=url, status=fetch_status, reason="", headers=[], body=b"")
    return {i["id"]: i for i in meta.run(ctx)}


GOOD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Running shoes for every distance | Example Shop</title>
<meta name="description" content="Light, cushioned running shoes for road and trail, with free returns and next-day delivery across the country.">
<link rel="canonical" href="https://shop.example.com/shoes">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.ico">
<meta property="og:title" content="Running shoes"><meta property="og:description" content="Shoes">
<meta property="og:image" content="https://shop.example.com/og.jpg"><meta property="og:url" content="https://shop.example.com/shoes">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization"},{"@type":"WebPage"}]}</script>
</head><body><h1>Shoes</h1></body></html>"""


class MetaChecks(unittest.TestCase):
    def test_good_page_passes_everything(self):
        res = run(GOOD)
        problems = {k: v["title"] for k, v in res.items() if v["severity"] != "pass"}
        self.assertEqual(problems, {})
        self.assertIn("Organization, WebPage", res["jsonld-ok"]["detail"])

    def test_empty_page(self):
        res = run("<p>hello</p>", headers=[])
        for key in ("title-missing", "description-missing", "canonical-missing", "viewport-missing", "lang-missing",
                    "charset-missing", "og-missing", "twitter-missing", "jsonld-missing"):
            self.assertIn(key, res)
        self.assertEqual(res["title-missing"]["severity"], "error")

    def test_favicon_broken_and_root_fallback(self):
        self.assertIn("favicon-broken", run(GOOD, fetch_status=404))
        no_icon = GOOD.replace('<link rel="icon" href="/favicon.ico">', "")
        self.assertIn("favicon-root", run(no_icon, fetch_status=200))
        self.assertIn("favicon-missing", run(no_icon, fetch_status=404))

    def test_noindex_in_header_for_googlebot(self):
        res = run(GOOD, headers=[("Content-Type", "text/html; charset=utf-8"), ("X-Robots-Tag", "googlebot: noindex, nofollow")])
        self.assertIn("robots-noindex", res)
        self.assertIn("robots-nofollow", res)
        self.assertNotIn("robots-ok", res)

    def test_noindex_for_other_bot_is_ignored(self):
        res = run(GOOD, headers=[("X-Robots-Tag", "otherbot: noindex"), ("X-Robots-Tag", "unavailable_after: 2030-01-01")])
        self.assertIn("robots-ok", res)

    def test_meta_none(self):
        res = run(GOOD.replace("<head>", '<head><meta name="robots" content="NONE">'))
        self.assertIn("robots-noindex", res)

    def test_canonical_cases(self):
        two = GOOD.replace('<link rel="icon"', '<link rel="canonical" href="https://shop.example.com/other"><link rel="icon"')
        self.assertEqual(run(two)["canonical-conflict"]["severity"], "error")
        rel = GOOD.replace('href="https://shop.example.com/shoes">', 'href="/shoes">', 1)
        self.assertIn("canonical-relative", run(rel))
        self.assertIn("canonical-ok", run(rel))
        self.assertIn("canonical-slash", run(GOOD.replace('href="https://shop.example.com/shoes">', 'href="https://shop.example.com/shoes/">', 1)))
        self.assertIn("canonical-elsewhere", run(GOOD.replace('href="https://shop.example.com/shoes">', 'href="https://shop.example.com/">', 1)))
        self.assertIn("canonical-http", run(GOOD.replace('href="https://shop.example.com/shoes">', 'href="http://shop.example.com/shoes">', 1)))
        hdr = run(GOOD.replace('<link rel="canonical" href="https://shop.example.com/shoes">', ""),
                  headers=[("Link", '<https://shop.example.com/shoes>; rel="canonical"')])
        self.assertIn("canonical-ok", hdr)

    def test_invalid_json_ld(self):
        res = run(GOOD.replace('"@type":"Organization"}', '"@type":"Organization",}'))
        self.assertEqual(res["jsonld-invalid-1"]["severity"], "error")
        self.assertIn("line 1", res["jsonld-invalid-1"]["detail"])

    def test_title_lengths_and_svg_title(self):
        res = run(GOOD.replace("Running shoes for every distance | Example Shop", "Shoes"))
        self.assertIn("title-short", res)
        res = run(GOOD.replace("<title>", "<title>" + "x" * 70))
        self.assertIn("title-long", res)
        svg_only = "<html><body><svg><title>icon</title></svg></body></html>"
        self.assertIn("title-missing", run(svg_only))

    def test_viewport_zoom_blocked(self):
        res = run(GOOD.replace("initial-scale=1", "initial-scale=1, maximum-scale=1, user-scalable=no"))
        self.assertIn("viewport-zoom", res)

    def test_parser_survives_missing_head_end(self):
        page = parse("<html><head><title>T</title><body><p>Visible words here</p>", URL)
        self.assertIn("Visible words here", page.text)


if __name__ == "__main__":
    unittest.main()
