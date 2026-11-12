"""Safety rules. Run with:  python -m unittest discover -s tests

Some tests need internet access for DNS (localtest.me, nip.io resolve to private addresses).
"""

import unittest
from unittest import mock

from seora import fetch
from seora.fetch import FetchError, Response


class UrlRules(unittest.TestCase):
    def refused(self, url, code):
        with self.assertRaises(FetchError) as cm:
            fetch.normalise_input(url)
        self.assertEqual(cm.exception.code, code, url)

    def test_schemes(self):
        for u in ("file:///etc/passwd", "ftp://example.com/", "javascript:alert(1)", "gopher://x/", "data:text/html,hi"):
            self.refused(u, "bad_scheme")

    def test_credentials_and_ports(self):
        self.refused("https://user:pw@example.com/", "bad_url")
        self.refused("http://example.com:22/", "bad_port")
        self.refused("http://example.com:6379/", "bad_port")

    def test_adds_https(self):
        self.assertEqual(fetch.normalise_input(" example.com "), "https://example.com/")
        self.assertEqual(fetch.normalise_input("example.com:8080/a b"), "https://example.com:8080/a%20b")
        self.assertEqual(fetch.normalise_input("https://EXAMPLE.com/#x"), "https://example.com/")


class AddressRules(unittest.TestCase):
    PRIVATE = ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0",
               "100.64.0.1", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
               "2002:7f00:1::", "::"]
    PUBLIC = ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"]

    def test_private(self):
        for ip in self.PRIVATE:
            self.assertFalse(fetch.is_public_ip(ip), ip)

    def test_public(self):
        for ip in self.PUBLIC:
            self.assertTrue(fetch.is_public_ip(ip), ip)

    def test_ip_literals_refused(self):
        for u in ("http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
                  "http://2130706433/", "http://0x7f000001/", "http://localhost/"):
            with self.assertRaises(FetchError) as cm:
                fetch.fetch(u)
            self.assertIn(cm.exception.code, ("blocked", "dns"), u)

    def test_public_names_that_point_inside(self):
        for u in ("http://localtest.me/", "http://10.0.0.1.nip.io/"):
            with self.assertRaises(FetchError) as cm:
                fetch.fetch(u)
            self.assertIn(cm.exception.code, ("blocked", "dns"), u)


class RedirectRules(unittest.TestCase):
    def test_redirect_to_private_is_refused(self):
        """A public page that redirects inside the network must be stopped at the second hop."""
        real = fetch._fetch_once
        calls = []

        def fake(url, *a, **kw):
            calls.append(url)
            if len(calls) == 1:
                return Response(url=url, status=302, reason="Found", headers=[("Location", "http://127.0.0.1/admin")], body=b"")
            return real(url, *a, **kw)

        with mock.patch.object(fetch, "_fetch_once", side_effect=fake):
            with self.assertRaises(FetchError) as cm:
                fetch.fetch("https://example.com/")
        self.assertEqual(cm.exception.code, "blocked")

    def test_redirect_to_other_scheme_is_refused(self):
        def fake(url, *a, **kw):
            return Response(url=url, status=301, reason="Moved", headers=[("Location", "file:///etc/passwd")], body=b"")

        with mock.patch.object(fetch, "_fetch_once", side_effect=fake):
            with self.assertRaises(FetchError) as cm:
                fetch.fetch("https://example.com/")
        self.assertEqual(cm.exception.code, "bad_scheme")

    def test_redirect_loop_and_limit(self):
        n = {"i": 0}

        def fake(url, *a, **kw):
            n["i"] += 1
            return Response(url=url, status=302, reason="Found", headers=[("Location", f"/p{n['i']}")], body=b"")

        with mock.patch.object(fetch, "_fetch_once", side_effect=fake):
            with self.assertRaises(FetchError) as cm:
                fetch.fetch("https://example.com/")
        self.assertEqual(cm.exception.code, "too_many_redirects")


class BodyLimits(unittest.TestCase):
    def test_gzip_bomb_is_capped(self):
        import gzip
        import io
        bomb = gzip.compress(b"\0" * 50_000_000)

        class R:
            def __init__(self):
                self.buf = io.BytesIO(bomb)

            def getheader(self, name):
                return "gzip" if name == "content-encoding" else None

            def read(self, n):
                return self.buf.read(n)

        body, wire, truncated, enc = fetch._read_body(R(), 1_000_000, float("inf"))
        self.assertEqual(len(body), 1_000_000)
        self.assertTrue(truncated)


if __name__ == "__main__":
    unittest.main()
