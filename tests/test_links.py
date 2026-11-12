"""Links check group on hand-made pages (no network). Run with:  python -m unittest discover -s tests"""

import unittest
from unittest import mock

from seora.audit import Context
from seora.checks import links
from seora.fetch import Hop, Response
from seora.parse import parse

URL = "https://example.com/page"
S = "https://example.com"


def web(pages):
    """A fake ctx.try_fetch. pages maps URL -> status, ("redirect", target, code), ("fail", code)
    or ("head404", status) for servers that answer HEAD wrongly. Unknown URLs answer 200."""
    calls = []

    def try_fetch(url, method="GET", **kw):
        calls.append((method, url))
        spec = pages.get(url, 200)
        chain = []
        if isinstance(spec, tuple) and spec[0] == "redirect":
            chain = [Hop(url, spec[2], spec[1])]
            url, spec = spec[1], pages.get(spec[1], 200)
        if isinstance(spec, tuple) and spec[0] == "fail":
            return None, spec[1]
        if isinstance(spec, tuple) and spec[0] == "head404":
            spec = 404 if method == "HEAD" else spec[1]
        return Response(url=url, status=spec, reason="", headers=[], body=b"", chain=chain), None

    try_fetch.calls = calls
    return try_fetch


def run(body, pages=None):
    html = f"<!doctype html><html><head><title>T</title></head><body>{body}</body></html>"
    resp = Response(url=URL, status=200, reason="OK", headers=[], body=html.encode("utf-8"))
    ctx = Context(requested_url=URL, final_url=URL, response=resp, page=parse(html, URL), html=html,
                  charset="utf-8", charset_source="header")
    ctx.try_fetch = web(pages or {})
    return {i["id"]: i for i in links.run(ctx)}, ctx.try_fetch.calls


class LinkChecks(unittest.TestCase):
    def test_good_links(self):
        res, _ = run('<a href="/a">About us</a><a href="https://www.example.com/b">Shop</a>'
                     '<a href="https://other.org/" rel="nofollow">Partner</a>')
        problems = {k: v["title"] for k, v in res.items() if v["severity"] != "pass"}
        self.assertEqual(problems, {})
        self.assertIn("2 links to this site and 1 link to other sites", res["links-count"]["detail"])
        self.assertIn("1 link to other sites is marked nofollow", res["links-nofollow-ok"]["detail"])

    def test_not_links(self):
        res, calls = run('<a href="#top">Top</a><a href="mailto:a@b.c">Mail</a><a href="tel:123">Call</a>'
                         '<a href="javascript:void(0)">JS</a><a>No href</a>')
        self.assertEqual(list(res), ["links-none"])
        self.assertIn("2 email or phone links", res["links-none"]["detail"])
        self.assertEqual(calls, [])

    def test_only_external(self):
        res, _ = run('<a href="https://other.org/">Other</a>')
        self.assertEqual(res["links-no-internal"]["severity"], "warning")

    def test_broken_refused_unknown(self):
        pages = {S + "/gone": 404, S + "/err": 500, "https://linkedin.com/in/x": 999,
                 "https://nosuch.example/": ("fail", "dns"), "https://slow.org/": ("fail", "timeout")}
        body = "".join(f'<a href="{u}">Link</a>' for u in ["/gone", "/err", "https://linkedin.com/in/x",
                                                          "https://nosuch.example/", "https://slow.org/", "/fine"])
        res, _ = run(body, pages)
        b = res["links-broken"]
        self.assertEqual(b["severity"], "error")
        self.assertEqual(b["title"], "3 broken links (2 on this site)")
        self.assertIn("/gone (404), /err (500), nosuch.example (site not found)", b["detail"])
        self.assertIn("linkedin.com/in/x (999)", res["links-refused"]["detail"])
        self.assertIn("slow.org", res["links-unchecked"]["detail"])

    def test_head_answered_wrongly_is_not_broken(self):
        res, calls = run('<a href="/x">X</a>', {S + "/x": ("head404", 200)})
        self.assertIn("links-ok", res)
        self.assertEqual(calls, [("HEAD", S + "/x"), ("GET", S + "/x")])

    def test_redirects(self):
        pages = {S + "/old": ("redirect", S + "/new", 301), "http://other.org/": ("redirect", "https://other.org/", 301),
                 S + "/moved-gone": ("redirect", S + "/nothing", 301), S + "/nothing": 404}
        res, _ = run('<a href="/old">Old</a><a href="http://other.org/">Other</a><a href="/moved-gone">Gone</a>', pages)
        r = res["links-redirect"]
        self.assertEqual(r["title"], "2 links go through a redirect")
        self.assertIn("/old → /new (301)", r["detail"])
        self.assertIn("1 of them goes to this site", r["detail"])
        self.assertIn("/moved-gone (404)", res["links-broken"]["detail"])  # redirect to a missing page is broken

    def test_each_address_once_and_this_page_skipped(self):
        res, calls = run('<a href="/a">A</a><a href="/a#x">A again</a><a href="/a">A</a><a href="/page">Here</a><a href="/page#s">Jump</a>')
        self.assertEqual(calls, [("HEAD", S + "/a")])

    def test_internal_nofollow(self):
        res, _ = run('<a href="/a" rel="nofollow">A</a><a href="/b" rel="ugc">B</a>')
        self.assertEqual(res["links-nofollow-internal"]["title"], "2 links to this site marked nofollow")

    def test_empty_text_and_stand_ins(self):
        body = ('<a href="/a"></a>'                                    # empty
                '<a href="/b"><img src="x.png"></a>'                   # image without alt: empty
                '<a href="/c"><img src="x.png" alt="Cart"></a>'        # image alt: fine
                '<a href="/d" aria-label="Search"><svg></svg></a>'     # aria-label: fine
                '<a href="https://twitter.com/x"><svg aria-label="Twitter"></svg></a>'  # icon label: fine
                '<a href="/e"><svg><title>Home</title></svg></a>'       # svg title: fine
                '<a href="/f" title="Help"> </a>')                      # title: fine
        res, _ = run(body)
        self.assertEqual(res["links-empty-text"]["title"], "2 links without text")
        self.assertIn("/a, /b", res["links-empty-text"]["detail"])

    def test_noscript_copies_not_counted(self):
        res, _ = run('<a href="/a">A</a><noscript><a href="/a"></a></noscript>')
        self.assertIn("links-text-ok", res)

    def test_internal_checked_first_and_cap(self):
        body = "".join(f'<a href="https://ext{n}.org/">E</a>' for n in range(5)) + "".join(f'<a href="/p{n}">P</a>' for n in range(5))
        with mock.patch("config.MAX_LINKS_CHECKED", 5):
            res, calls = run(body)
        self.assertEqual(sorted(u for _, u in calls), sorted(S + f"/p{n}" for n in range(5)))
        self.assertIn("5 more addresses were not checked", res["links-ok"]["detail"])


if __name__ == "__main__":
    unittest.main()
