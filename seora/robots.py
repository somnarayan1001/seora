"""robots.txt reader that follows Google's rules (RFC 9309).

Python's urllib.robotparser uses the first matching rule; Google and RFC 9309 use the longest
match, with Allow winning a tie, and support * and $ in paths. This module does the latter.
It is used by the Technical checks now, and by the whole-site crawler later (to stay polite).
"""

import re
from urllib.parse import urlsplit

KNOWN_FIELDS = {"user-agent", "allow", "disallow", "sitemap", "crawl-delay", "host", "clean-param",
                "request-rate", "visit-time", "noindex"}
MAX_BYTES = 500 * 1024  # Google reads only the first 500 KiB


class Robots:
    def __init__(self, text):
        self.groups = []          # [(agents set, [(allow, pattern)])]
        self.sitemaps = []
        self.problems = []        # [(line number, line)] lines that aren't valid robots.txt
        self.orphan_rules = 0     # Allow/Disallow lines before any User-agent line
        self._parse(text or "")

    def _parse(self, text):
        agents, rules, in_rules = None, None, False
        for n, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                self.problems.append((n, raw.strip()))
                continue
            field, value = line.split(":", 1)
            field, value = field.strip().lower(), value.strip()
            if field not in KNOWN_FIELDS:
                # common misspellings Google accepts
                field = {"useragent": "user-agent", "user agent": "user-agent", "dissallow": "disallow",
                         "disalow": "disallow", "site-map": "sitemap"}.get(field, field)
            if field == "user-agent":
                if agents is None or in_rules:
                    agents, rules, in_rules = set(), [], False
                    self.groups.append((agents, rules))
                agents.add(value.lower())
            elif field in ("allow", "disallow"):
                if agents is None:
                    self.orphan_rules += 1
                    continue
                in_rules = True
                if value:  # an empty Disallow means "nothing is disallowed"
                    rules.append((field == "allow", value))
            elif field == "sitemap":
                if value:
                    self.sitemaps.append(value)
            elif field in KNOWN_FIELDS:
                if agents is not None:
                    in_rules = True
            else:
                self.problems.append((n, raw.strip()))

    def rules_for(self, agent):
        """Rules of the most specific group for this crawler (merged if it appears several times), else '*'."""
        agent = agent.lower()
        best, best_len = [], -1
        for agents, rules in self.groups:
            for a in agents:
                if a != "*" and a in agent and len(a) > best_len:
                    best_len = len(a)
        if best_len >= 0:
            for agents, rules in self.groups:
                if any(a != "*" and a in agent and len(a) == best_len for a in agents):
                    best = best + rules
            return best
        for agents, rules in self.groups:
            if "*" in agents:
                best = best + rules
        return best

    def allowed(self, url, agent="googlebot"):
        """(allowed, matching rule or None) for a URL or path."""
        p = urlsplit(url)
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        match, match_len = None, -1
        for allow, pattern in self.rules_for(agent):
            if _matches(pattern, path):
                if len(pattern) > match_len or (len(pattern) == match_len and allow):
                    match, match_len = (allow, pattern), len(pattern)
        if match is None:
            return True, None
        return match[0], ("Allow: " if match[0] else "Disallow: ") + match[1]


_cache = {}


def _matches(pattern, path):
    rx = _cache.get(pattern)
    if rx is None:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        rx = re.compile("".join(".*" if c == "*" else re.escape(c) for c in body) + ("$" if anchored else ""))
        if len(_cache) < 2000:
            _cache[pattern] = rx
    return rx.match(path) is not None
