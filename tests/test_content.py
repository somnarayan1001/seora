"""Content check group on hand-made pages (no network). Run with:  python -m unittest discover -s tests"""

import unittest

from seora.audit import Context
from seora.checks import content
from seora.fetch import Response
from seora.parse import parse

URL = "https://example.com/guide"


def run(body, head="<title>T</title>"):
    html = f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"
    resp = Response(url=URL, status=200, reason="OK", headers=[], body=html.encode("utf-8"))
    ctx = Context(requested_url=URL, final_url=URL, response=resp, page=parse(html, URL), html=html,
                  charset="utf-8", charset_source="header")
    ctx.fetch = lambda url, **kw: None
    return {i["id"]: i for i in content.run(ctx)}


WORDS = " ".join(["word"] * 400)


class ContentChecks(unittest.TestCase):
    def test_good_page(self):
        res = run(f"<h1>Guide</h1><h2>Part</h2><h3>Bit</h3><h2>Other</h2><p>{WORDS}</p>")
        problems = {k: v["title"] for k, v in res.items() if v["severity"] != "pass"}
        self.assertEqual(problems, {})
        self.assertIn("1 × H1, 2 × H2, 1 × H3", res["headings-ok"]["detail"])

    def test_h1_missing_multiple_empty(self):
        self.assertEqual(run(f"<h2>A</h2><p>{WORDS}</p>")["h1-missing"]["severity"], "error")
        self.assertIn("h1-multiple", run(f"<h1>A</h1><h1>B</h1><p>{WORDS}</p>"))
        self.assertIn("h1-empty", run(f"<h1> </h1><p>{WORDS}</p>"))

    def test_logo_image_in_h1_counts(self):
        res = run(f'<h1><img src="/logo.png" alt="Acme Tools"></h1><p>{WORDS}</p>')
        self.assertIn("h1-ok", res)
        self.assertIn("Acme Tools", res["h1-ok"]["detail"])

    def test_svg_logo_in_h1_counts(self):
        # As on bbc.com/news: an SVG logo named with aria-label
        res = run(f'<h1><svg aria-label="News" viewBox="0 0 1 1"><path d="M0 0"/></svg></h1><p>{WORDS}</p>')
        self.assertEqual(res["h1-ok"]["detail"], "“News”")
        res = run(f'<h1><svg viewBox="0 0 1 1"><title>Acme</title></svg></h1><p>{WORDS}</p>')
        self.assertEqual(res["h1-ok"]["detail"], "“Acme”")
        # real text wins over the label
        res = run(f'<h1 aria-label="Label">Real text</h1><p>{WORDS}</p>')
        self.assertEqual(res["h1-ok"]["detail"], "“Real text”")

    def test_textarea_templates_not_counted(self):
        # As on baidu.com: HTML templates kept in hidden textareas
        res = run('<h1>A</h1><textarea style="display:none">' + "<style>p{}</style> " * 500 + "</textarea>")
        self.assertIn("content-thin", res)

    def test_skipped_levels(self):
        res = run(f"<h1>A</h1><h3>Jump</h3><h2>B</h2><h4>Jump 2</h4><p>{WORDS}</p>")
        self.assertEqual(res["headings-skipped"]["title"], "Heading levels are skipped 2 times")
        self.assertIn("H1 → H3 at “Jump”", res["headings-skipped"]["detail"])
        # going back up (H4 then H2) is fine
        self.assertNotIn("headings-skipped", run(f"<h1>A</h1><h2>B</h2><h3>C</h3><h4>D</h4><h2>E</h2><p>{WORDS}</p>"))

    def test_first_heading_h3_skips(self):
        self.assertIn("headings-skipped", run(f"<h3>Start</h3><p>{WORDS}</p>"))

    def test_no_headings_and_empty_headings(self):
        self.assertIn("headings-missing", run(f"<p>{WORDS}</p>"))
        self.assertIn("headings-empty", run(f"<h1>A</h1><h2></h2><p>{WORDS}</p>"))

    def test_thin_content_and_js_hint(self):
        res = run("<h1>App</h1><div id=root></div><script src='/app.js'></script>")
        self.assertIn("content-thin", res)
        self.assertIn("JavaScript", res["content-thin"]["detail"])
        self.assertNotIn("JavaScript after", run("<h1>Hi</h1><p>A few words only.</p>")["content-thin"]["detail"])

    def test_hidden_text_not_counted(self):
        res = run(f"<h1>A</h1><script>var x = '{WORDS}';</script><style>p{{}}</style><noscript>{WORDS}</noscript>")
        self.assertIn("content-thin", res)

    def test_cjk_word_count(self):
        page = parse("<body><p>" + "搜索引擎优化" * 100 + "</p></body>", URL)
        self.assertEqual(page.word_count, 300)

    def test_text_ratio(self):
        bloat = "<script>" + "x" * 50000 + "</script>"
        res = run(f"<h1>A</h1><p>{WORDS}</p>{bloat}")
        self.assertIn("text-ratio-low", res)
        self.assertIn("text-ratio-ok", run(f"<h1>A</h1><p>{WORDS}</p>"))


if __name__ == "__main__":
    unittest.main()
