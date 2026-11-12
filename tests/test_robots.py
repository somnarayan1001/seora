"""robots.txt reader, checked against the examples in Google's robots.txt documentation and RFC 9309."""

import unittest

from seora.robots import Robots


class RobotsRules(unittest.TestCase):
    def test_longest_match_wins_and_allow_wins_ties(self):
        r = Robots("User-agent: *\nDisallow: /shop\nAllow: /shop/public\nAllow: /page\nDisallow: /page\n")
        self.assertFalse(r.allowed("https://x.com/shop/cart")[0])
        self.assertTrue(r.allowed("https://x.com/shop/public/a")[0])
        self.assertTrue(r.allowed("https://x.com/page")[0])          # same length: Allow wins
        self.assertTrue(r.allowed("https://x.com/other")[0])

    def test_wildcards_and_end(self):
        r = Robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?sessionid=\n")
        self.assertFalse(r.allowed("https://x.com/files/a.pdf")[0])
        self.assertTrue(r.allowed("https://x.com/files/a.pdf?download=1")[0])
        self.assertFalse(r.allowed("https://x.com/list?sessionid=9")[0])

    def test_most_specific_group(self):
        r = Robots("User-agent: *\nDisallow: /\n\nUser-agent: Googlebot\nAllow: /\nDisallow: /private\n")
        self.assertTrue(r.allowed("https://x.com/a", "googlebot")[0])
        self.assertFalse(r.allowed("https://x.com/private", "googlebot")[0])
        self.assertFalse(r.allowed("https://x.com/a", "bingbot")[0])

    def test_groups_for_same_agent_are_merged_and_shared_lines(self):
        r = Robots("User-agent: a\nUser-agent: googlebot\nDisallow: /x\n\nUser-agent: googlebot\nDisallow: /y\n")
        self.assertFalse(r.allowed("/x")[0])
        self.assertFalse(r.allowed("/y")[0])

    def test_empty_disallow_allows_everything(self):
        r = Robots("User-agent: *\nDisallow:\n")
        self.assertTrue(r.allowed("/anything")[0])

    def test_sitemaps_comments_and_problems(self):
        r = Robots("# hi\nSitemap: https://x.com/s.xml\nDisallow: /early\nUser-agent: * # all\nDisallow: /a # note\nNonsense line\nFoo: bar\nCrawl-delay: 5\n")
        self.assertEqual(r.sitemaps, ["https://x.com/s.xml"])
        self.assertEqual(r.orphan_rules, 1)
        self.assertEqual([n for n, _ in r.problems], [6, 7])
        self.assertFalse(r.allowed("/a")[0])
        self.assertEqual(r.allowed("/a")[1], "Disallow: /a")

    def test_no_rules(self):
        self.assertEqual(Robots("").allowed("/x"), (True, None))


if __name__ == "__main__":
    unittest.main()
