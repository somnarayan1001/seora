"""Page loading check group on hand-made pages (no network). Run with:  python -m unittest discover -s tests"""

import unittest

from seora.audit import Context
from seora.checks import images, loading
from seora.fetch import Hop, Response
from seora.parse import parse

URL = "https://example.com/page"
S = "https://example.com"


def files(sizes):
    """A fake ctx.fetch answering HEAD with Content-Length from sizes (URL -> bytes); others 404."""
    calls = []

    def fetch(url, method="GET", **kw):
        calls.append((method, url))
        if url not in sizes:
            return Response(url=url, status=404, reason="", headers=[], body=b"")
        return Response(url=url, status=200, reason="", headers=[("Content-Length", str(sizes[url])), ("Content-Type", "image/webp")], body=b"")

    fetch.calls = calls
    return fetch


def make(head="", body="", sizes=None, ttfb=200, download=50, html_size=None, transfer=None, chain=None, total=None, connect=0):
    html = f"<!doctype html><html><head><title>T</title>{head}</head><body>{body}</body></html>"
    raw = html.encode("utf-8") + (b" " * (html_size - len(html)) if html_size else b"")
    resp = Response(url=URL, status=200, reason="OK", headers=[], body=raw, transfer_bytes=transfer or len(raw),
                    ttfb_ms=ttfb, download_ms=download, connect_ms=connect, total_ms=total or ttfb + download, chain=chain or [])
    ctx = Context(requested_url=URL, final_url=URL, response=resp, page=parse(html, URL), html=html,
                  charset="utf-8", charset_source="header")
    ctx.fetch = files(sizes or {})
    return ctx


def run(**kw):
    ctx = make(**kw)
    return {i["id"]: i for i in loading.run(ctx)}, ctx


class LoadingChecks(unittest.TestCase):
    def test_light_fast_page(self):
        res, _ = run(head='<link rel="stylesheet" href="/s.css"><script src="/a.js" defer></script>',
                     body='<img src="/i.webp" alt="x" width=1 height=2>',  # 1 px wide: a tracking pixel, ignored
                     sizes={S + "/s.css": 20_000, S + "/a.js": 80_000})
        problems = {k: v["title"] for k, v in res.items() if v["severity"] != "pass"}
        self.assertEqual(problems, {})
        self.assertIn("1 script (80 KB); 1 stylesheet (20 KB)", res["weight-ok"]["detail"])
        self.assertTrue(res["heaviest"]["detail"].startswith("a.js (script, 80 KB), s.css (stylesheet, 20 KB)."))

    def test_ttfb_levels_and_redirect_time(self):
        self.assertEqual(run(ttfb=1200)[0]["ttfb-medium"]["severity"], "notice")
        self.assertEqual(run(ttfb=2500)[0]["ttfb-slow"]["severity"], "warning")
        res, _ = run(ttfb=300, download=100, total=900, chain=[Hop("http://example.com/page", 301, URL)])
        self.assertIn("Redirects before it added 500 ms", res["ttfb-ok"]["detail"])
        self.assertIn("download-slow", run(download=1500)[0])
        # a far-away site: slow to connect, quick to answer -> not the site's fault
        res, _ = run(ttfb=1600, connect=1300)
        self.assertEqual(res["ttfb-ok"]["title"], "Server answers quickly (300 ms)")
        self.assertIn("1.3 s to connect", res["ttfb-ok"]["detail"])

    def test_html_weight(self):
        self.assertIn("html-big", run(html_size=400_000, transfer=60_000)[0])
        res, _ = run(html_size=1_200_000, transfer=200_000)
        self.assertEqual(res["html-huge"]["severity"], "warning")
        self.assertIn("200 KB over the network", res["html-huge"]["detail"])

    def test_heavy_page_counts_only_first_load(self):
        body = "".join(f'<img src="/{n}.webp" alt="x">' for n in range(3))
        body += "".join(f'<img src="/l{n}.webp" alt="x" loading="lazy">' for n in range(3))
        sizes = {S + f"/{n}.webp": 1_000_000 for n in range(3)} | {S + f"/l{n}.webp": 1_000_000 for n in range(3)}
        res, _ = run(body=body, sizes=sizes)
        self.assertEqual(res["weight-big"]["title"], "The page is heavy (3.0 MB)")
        self.assertIn("3 of them loaded only when scrolled to", res["weight-big"]["detail"])
        sizes = {k: 2_000_000 for k in sizes}
        self.assertEqual(run(body=body, sizes=sizes)[0]["weight-huge"]["severity"], "warning")

    def test_unmeasured_files_are_mentioned(self):
        res, _ = run(head='<script src="/gone.js" defer></script>')
        self.assertIn("1 file couldn't be measured", res["weight-ok"]["detail"])

    def test_javascript_weight(self):
        head = "".join(f'<script src="/{n}.js" defer></script>' for n in range(4))
        sizes = {S + f"/{n}.js": 200_000 for n in range(4)}
        self.assertEqual(run(head=head, sizes=sizes)[0]["js-big"]["title"], "Large amount of JavaScript (800 KB)")
        sizes = {k: 300_000 for k in sizes}
        self.assertIn("js-huge", run(head=head, sizes=sizes)[0])

    def test_render_blocking(self):
        head = ('<script src="/block.js"></script><script src="/ok1.js" async></script><script src="/ok2.js" defer></script>'
                '<script type="module" src="/m.js"></script><script type="application/ld+json">{}</script><script>inline()</script>')
        res, _ = run(head=head, body='<script src="/end.js"></script>')
        self.assertEqual(res["render-blocking"]["title"], "1 script in <head> holds up the first display")
        self.assertIn("block.js", res["render-blocking"]["detail"])
        self.assertNotIn("end.js", res["render-blocking"]["detail"])

    def test_names_tell_files_apart(self):
        head = '<script src="https://www.googletagmanager.com/gtag/js?id=1" defer></script>'
        body = '<img src="/a/hero.jpg" alt="x"><img src="/b/hero.jpg" alt="x">'
        sizes = {"https://www.googletagmanager.com/gtag/js?id=1": 150_000, S + "/a/hero.jpg": 90_000, S + "/b/hero.jpg": 80_000}
        res, _ = run(head=head, body=body, sizes=sizes)
        self.assertIn("www.googletagmanager.com/js (script, 150 KB), a/hero.jpg (image, 90 KB), b/hero.jpg", res["heaviest"]["detail"])
        res, _ = run(head=head + '<script src="/gone.js" defer></script>', sizes=sizes)
        self.assertIn("1 of 2 script files measured", res["js-ok"]["detail"])

    def test_many_files(self):
        body = "".join(f'<img src="/{n}.webp" alt="x" loading="lazy">' for n in range(90))
        self.assertIn("files-many", run(body=body)[0])

    def test_image_sizes_reused_from_images_group(self):
        ctx = make(body='<img src="/a.webp" alt="x" width=9 height=9><img src="/b.webp" alt="y" width=9 height=9>',
                   sizes={S + "/a.webp": 5000, S + "/b.webp": 7000})
        images.run(ctx)
        before = len(ctx.fetch.calls)
        res = {i["id"]: i for i in loading.run(ctx)}
        self.assertEqual(len(ctx.fetch.calls), before)  # nothing fetched twice
        self.assertIn("2 images (12 KB)", res["weight-ok"]["detail"])


if __name__ == "__main__":
    unittest.main()
