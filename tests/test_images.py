"""Images check group on hand-made pages (no network). Run with:  python -m unittest discover -s tests"""

import unittest
from unittest import mock

from seora.audit import Context
from seora.checks import images
from seora.fetch import Response
from seora.parse import parse

URL = "https://example.com/page"


def server(files):
    """A fake ctx.fetch. files maps URL -> (status, size, content type, mode).
    mode 'head' answers HEAD with Content-Length; 'range' refuses HEAD and answers ranged GETs with 206;
    'full' gives no length on HEAD and ignores Range."""
    calls = []

    def fetch(url, method="GET", extra_headers=None, **kw):
        calls.append((method, url))
        status, size, ctype, mode = files.get(url, (404, 0, "text/html", "head"))
        h = [("Content-Type", ctype)]
        if status >= 400:
            return Response(url=url, status=status, reason="", headers=h, body=b"")
        if method == "HEAD":
            if mode == "range":
                return Response(url=url, status=405, reason="", headers=h, body=b"")
            if mode == "head":
                h.append(("Content-Length", str(size)))
            return Response(url=url, status=200, reason="", headers=h, body=b"")
        if mode == "range" and extra_headers and "Range" in extra_headers:
            return Response(url=url, status=206, reason="", headers=h + [("Content-Range", f"bytes 0-0/{size}")], body=b"x")
        return Response(url=url, status=200, reason="", headers=h, body=b"x" * size, transfer_bytes=size)

    fetch.calls = calls
    return fetch


def run(body, files=None):
    html = f"<!doctype html><html><head><title>T</title></head><body>{body}</body></html>"
    resp = Response(url=URL, status=200, reason="OK", headers=[], body=html.encode("utf-8"))
    ctx = Context(requested_url=URL, final_url=URL, response=resp, page=parse(html, URL), html=html,
                  charset="utf-8", charset_source="header")
    ctx.fetch = server(files or {})
    return {i["id"]: i for i in images.run(ctx)}, ctx.fetch.calls


def img(name, extra='alt="x" width="10" height="10"'):
    return f'<img src="/{name}" {extra}>'


A = "https://example.com/"


class ImageChecks(unittest.TestCase):
    def test_no_images(self):
        res, calls = run("<p>Hi</p>")
        self.assertEqual(list(res), ["images-none"])
        self.assertEqual(calls, [])

    def test_good_images(self):
        files = {A + "a.webp": (200, 30_000, "image/webp", "head"), A + "b.svg": (200, 2_000, "image/svg+xml", "head")}
        res, _ = run(img("a.webp") + img("b.svg"), files)
        problems = {k: v["title"] for k, v in res.items() if v["severity"] != "pass"}
        self.assertEqual(problems, {})

    def test_alt_missing_empty_and_decorative(self):
        body = ('<img src="/a.webp" width=1 height=1>'           # tracking pixel: ignored
                '<img src="/b.webp" width=9 height=9>'            # missing alt
                '<img src="/c.webp" alt="" width=9 height=9>'     # empty alt
                '<img src="/d.webp" role="presentation" width=9 height=9>'   # decorative, no alt: fine
                '<img src="/e.webp" aria-label="image unavailable" width=9 height=9>')  # labelled, as on bbc.com: fine
        res, _ = run(body, {A + n: (200, 100, "image/webp", "head") for n in ("b.webp", "c.webp", "d.webp", "e.webp")})
        self.assertEqual(res["img-alt-missing"]["title"], "1 image without alt text")
        self.assertIn("b.webp", res["img-alt-missing"]["detail"])
        self.assertIn("c.webp", res["img-alt-empty"]["detail"])

    def test_broken_and_sizes_with_each_server_kind(self):
        files = {A + "big.jpg": (200, 450_000, "image/jpeg", "range"),
                 A + "huge.png": (200, 1_200_000, "image/png", "full"),
                 A + "ok.webp": (200, 50_000, "image/webp", "head")}
        res, calls = run(img("big.jpg") + img("huge.png") + img("ok.webp") + img("gone.jpg"), files)
        self.assertEqual(res["img-broken"]["severity"], "error")
        self.assertIn("gone.jpg (404)", res["img-broken"]["detail"])
        self.assertIn("huge.png (1.2 MB), big.jpg (450 KB)", res["img-large"]["detail"])
        self.assertIn("2 images could use WebP or AVIF", res["img-format"]["title"])
        self.assertIn(("GET", A + "big.jpg"), calls)   # HEAD refused, so a ranged GET followed

    def test_each_image_fetched_once(self):
        files = {A + "a.webp": (200, 1000, "image/webp", "head")}
        _, calls = run(img("a.webp") * 5, files)
        self.assertEqual(calls, [("HEAD", A + "a.webp")])

    def test_picture_with_webp_source_is_fine(self):
        body = '<picture><source type="image/webp" srcset="/a.webp">' + img("a.jpg") + "</picture>"
        res, _ = run(body, {A + "a.jpg": (200, 80_000, "image/jpeg", "head")})
        self.assertIn("img-format-ok", res)

    def test_server_negotiated_webp_is_fine(self):
        # The address ends in .jpg but the server sends WebP to browsers that accept it
        res, _ = run(img("a.jpg"), {A + "a.jpg": (200, 80_000, "image/webp", "head")})
        self.assertIn("img-format-ok", res)

    def test_small_old_format_not_flagged(self):
        res, _ = run(img("icon.png"), {A + "icon.png": (200, 3_000, "image/png", "head")})
        self.assertIn("img-format-ok", res)

    def test_dimensions(self):
        res, _ = run(img("a.webp", 'alt="x" width="10"'), {A + "a.webp": (200, 100, "image/webp", "head")})
        self.assertEqual(res["img-dimensions"]["severity"], "notice")

    def test_lazy_loading(self):
        files = {A + f"{n}.webp": (200, 100, "image/webp", "head") for n in range(6)}
        eager = "".join(img(f"{n}.webp") for n in range(6))
        res, _ = run(eager, files)
        self.assertEqual(res["img-lazy-missing"]["title"], "3 images further down load straight away")
        lazy_top = img("0.webp", 'alt="x" width=9 height=9 loading="lazy"')
        rest = "".join(img(f"{n}.webp", 'alt="x" width=9 height=9 loading="lazy"') for n in range(1, 6))
        res, _ = run(lazy_top + rest, files)
        self.assertIn("img-lazy-top", res)
        self.assertNotIn("img-lazy-missing", res)

    def test_script_lazy_loading_and_noscript_fallback(self):
        # A lazy-loading script: placeholder src, real address in data-src, plus a <noscript> copy
        files = {A + f"{n}.webp": (200, 100, "image/webp", "head") for n in range(5)}
        body = "".join(f'<img src="data:image/gif;base64,R0lGOD" data-src="/{n}.webp" alt="x" width=9 height=9>'
                       f'<noscript><img src="/{n}.webp"></noscript>' for n in range(5))
        res, calls = run(body, files)
        self.assertNotIn("img-alt-missing", res)        # the noscript copies aren't counted
        self.assertNotIn("img-lazy-missing", res)
        self.assertIn("img-lazy-top", res)               # the first ones are lazy too
        self.assertEqual(len(calls), 5)                  # the real addresses were measured

    def test_cap_and_time_budget(self):
        files = {A + f"{n}.webp": (200, 100, "image/webp", "head") for n in range(10)}
        with mock.patch("config.MAX_IMAGES_CHECKED", 4):
            res, calls = run("".join(img(f"{n}.webp") for n in range(10)), files)
        self.assertEqual(len(calls), 4)
        self.assertIn("6 more weren't checked", res["img-load-ok"]["detail"])

    def test_no_answer(self):
        # Every fetch fails (timeout, refused...): unknown, not broken
        html = "<html><body>" + img("slow.jpg") + "</body></html>"
        resp = Response(url=URL, status=200, reason="OK", headers=[], body=html.encode())
        ctx = Context(requested_url=URL, final_url=URL, response=resp, page=parse(html, URL), html=html, charset="utf-8", charset_source="header")
        ctx.fetch = lambda url, **kw: None
        res = {i["id"]: i for i in images.run(ctx)}
        self.assertIn("img-unchecked", res)
        self.assertNotIn("img-broken", res)


if __name__ == "__main__":
    unittest.main()
