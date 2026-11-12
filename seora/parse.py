"""Page model built with html.parser.

parse(html, url) returns a Page with everything the checks need. The parser is
forgiving: broken HTML still gives a usable Page, like a browser would.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

# Text inside these is not visible on the page
# (textarea: form-field content, and some sites keep hidden HTML templates in them)
HIDDEN = {"script", "style", "noscript", "template", "head", "svg", "math", "iframe", "object", "textarea"}
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# Elements that start a new line, so words on either side don't run together
BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "section", "article", "header",
         "footer", "nav", "aside", "main", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
         "dd", "dt", "dl", "figure", "figcaption", "form", "label", "option", "hr", "address"}
# Elements that load another file (besides img, script and link)
MEDIA = {"iframe", "video", "audio", "source", "embed", "object", "track"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


@dataclass
class Page:
    url: str
    html_length: int = 0
    lang: str = None              # None: no lang attribute; "" : present but empty
    titles: list = field(default_factory=list)       # text of every <title>
    metas: list = field(default_factory=list)        # attribute dicts, plus "_pos" (character offset)
    links: list = field(default_factory=list)        # <link> attribute dicts, plus "url" (absolute href)
    anchors: list = field(default_factory=list)      # {href, url, text, rel, img_alt}
    images: list = field(default_factory=list)       # <img> attribute dicts, plus "url"
    headings: list = field(default_factory=list)     # (level, text)
    scripts: list = field(default_factory=list)      # {src, url, type, async, defer, text (inline only)}
    media: list = field(default_factory=list)        # {tag, url, poster} for iframe, video, audio, source, embed, object, track
    base_href: str = None
    text: str = ""                                   # visible body text

    # ---- helpers for checks ----
    @property
    def title(self):
        return squash(self.titles[0]) if self.titles else None

    def meta(self, name):
        """content= of every <meta name=...> with this name (case-insensitive)."""
        name = name.lower()
        return [m.get("content") or "" for m in self.metas if (m.get("name") or "").strip().lower() == name]

    def meta_property(self, prop):
        """content= of every <meta property=...> (Open Graph uses property, some sites use name)."""
        prop = prop.lower()
        return [m.get("content") or "" for m in self.metas
                if (m.get("property") or m.get("name") or "").strip().lower() == prop]

    def links_with_rel(self, rel):
        rel = rel.lower()
        return [l for l in self.links if rel in (l.get("rel") or "").lower().split()]

    @property
    def json_ld(self):
        return [s for s in self.scripts if (s.get("type") or "").strip().lower() == "application/ld+json"]

    @property
    def word_count(self):
        # Chinese and Japanese don't put spaces between words: count those characters
        # separately, at about two characters per word
        cjk = len(_CJK.findall(self.text))
        rest = _CJK.sub(" ", self.text)
        return len(re.findall(r"\w+", rest)) + (cjk + 1) // 2


class _Parser(HTMLParser):
    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.page = page
        self.hidden = 0           # depth inside hidden elements
        self.in_body = False
        self.title_buf = None
        self.heading = None       # [level, [text...], [labels...]]
        self.svg_title = False    # inside an <svg><title> that sits in a heading
        self.anchor = None        # current anchor dict being filled
        self.script = None        # current inline script dict
        self.picture = None       # [<source> types] while inside <picture>
        self.text = []
        self._hidden_tags = []    # stack of open hidden elements

    def _url(self, href):
        if href is None:
            return None
        href = href.strip()
        if not href:
            return ""
        try:
            return urljoin(self.page.base_href or self.page.url, href)
        except ValueError:
            return ""

    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            if k not in a:          # first one wins, as in browsers
                a[k] = v if v is not None else ""
        p = self.page
        if tag == "html" and "lang" in a and p.lang is None:
            p.lang = a["lang"].strip()
        elif tag == "body":
            self.in_body = True
            self._leave_head()
        elif tag == "base" and p.base_href is None and a.get("href"):
            p.base_href = urljoin(p.url, a["href"].strip())
        elif tag == "title" and self.title_buf is None and not self.in_svg():
            self.title_buf = []
        elif tag == "meta":
            a["_pos"] = self.getpos()
            p.metas.append(a)
        elif tag == "link":
            a["url"] = self._url(a.get("href"))
            p.links.append(a)
        elif tag == "a":
            self._close_anchor()
            self.anchor = {"href": a.get("href"), "url": self._url(a.get("href")), "rel": a.get("rel", ""),
                           "text": [], "img_alt": [], "title": a.get("title", ""),
                           "aria_label": a.get("aria-label", ""), "labels": [],
                           # copies inside <noscript>/<template> aren't shown when scripts run
                           "_copy": bool({"noscript", "template"} & set(self._hidden_tags))}
        elif tag in MEDIA:
            src = a.get("data") if tag == "object" else a.get("src")
            if src or a.get("poster") or a.get("srcset"):
                p.media.append({"tag": tag, "url": self._url(src) if src else "", "poster": self._url(a.get("poster")),
                                "srcset": a.get("srcset") or ""})
            if tag == "source" and self.picture is not None:
                self.picture.append((a.get("type") or "").strip().lower())
        elif tag == "picture":
            self.picture = []
        elif tag == "img":
            a["url"] = self._url(a.get("src"))
            # The <source> formats offered next to this image inside <picture>
            a["_picture_types"] = list(self.picture) if self.picture is not None else None
            # Fallback copies inside <noscript> and <template> aren't shown when scripts run; don't count them twice
            if not {"noscript", "template"} & set(self._hidden_tags):
                p.images.append(a)
            if self.anchor is not None and a.get("alt"):
                self.anchor["img_alt"].append(a["alt"])
            if self.heading is not None and a.get("alt") and not self.hidden:
                # A logo image in an H1 counts: search engines read its alt text as the heading
                self.heading[1].append(" " + a["alt"] + " ")
        elif tag in HEADINGS:
            self._close_heading()
            self.heading = [HEADINGS[tag], [], []]
        elif tag == "script":
            s = {"src": a.get("src"), "url": self._url(a.get("src")), "type": a.get("type", ""),
                 "async": "async" in a, "defer": "defer" in a, "text": None}
            p.scripts.append(s)
            if not a.get("src"):
                self.script = s
                s["text"] = ""
        if self.heading is not None and a.get("aria-label"):
            # An icon or SVG logo labelled for screen readers names the heading when it has no text
            self.heading[2].append(a["aria-label"])
        if self.anchor is not None and tag != "a" and a.get("aria-label"):
            # icon links: <a><svg aria-label="Twitter">…</svg></a>
            self.anchor["labels"].append(a["aria-label"])
        if tag == "title" and (self.heading is not None or self.anchor is not None) and self.in_svg():
            self.svg_title = True
        if tag in BLOCK:
            self._add_text("\n")
        if tag in HIDDEN and tag not in VOID:
            self.hidden += 1
            self._hidden_tags.append(tag)

    def in_svg(self):
        return "svg" in self._hidden_tags

    def _leave_head(self):
        # A missing </head> must not hide the whole body
        if "head" in self._hidden_tags:
            while self._hidden_tags:
                self.hidden -= 1
                if self._hidden_tags.pop() == "head":
                    break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "title" and self.svg_title:
            self.svg_title = False
        elif tag == "title" and self.title_buf is not None:
            self.page.titles.append("".join(self.title_buf))
            self.title_buf = None
        elif tag == "a":
            self._close_anchor()
        elif tag in HEADINGS:
            self._close_heading()
        elif tag == "script":
            self.script = None
        elif tag == "picture":
            self.picture = None
        elif tag == "head":
            # Some pages forget <body>; text after </head> is body text
            self.in_body = True
        if tag in HIDDEN and self._hidden_tags and tag in self._hidden_tags:
            # pop back to the matching element, so a missing end tag can't hide the rest of the page
            while self._hidden_tags:
                self.hidden -= 1
                if self._hidden_tags.pop() == tag:
                    break
        if tag in BLOCK:
            self._add_text("\n")

    def handle_data(self, data):
        if self.title_buf is not None:
            self.title_buf.append(data)
            return
        if self.script is not None:
            self.script["text"] += data
            return
        if self.svg_title:
            if self.heading is not None:
                self.heading[2].append(data)
            if self.anchor is not None:
                self.anchor["labels"].append(data)
            return
        if self.hidden:
            return
        self._add_text(data)
        if self.anchor is not None:
            self.anchor["text"].append(data)
        if self.heading is not None:
            self.heading[1].append(data)

    def _add_text(self, data):
        if not self.hidden:
            self.text.append(data)

    def _close_anchor(self):
        if self.anchor is not None:
            a = self.anchor
            a["text"] = squash("".join(a["text"]))
            a["img_alt"] = squash(" ".join(a["img_alt"]))
            a["labels"] = " ".join(dict.fromkeys(squash(l) for l in a["labels"] if squash(l)))
            if not a.pop("_copy"):
                self.page.anchors.append(a)
            self.anchor = None

    def _close_heading(self):
        if self.heading is not None:
            labels = list(dict.fromkeys(squash(l) for l in self.heading[2] if squash(l)))  # a logo often has the same name twice
            text = squash("".join(self.heading[1])) or " ".join(labels)
            self.page.headings.append((self.heading[0], text))
            self.heading = None

    def finish(self):
        self.close()
        if self.title_buf is not None:
            self.page.titles.append("".join(self.title_buf))
        self._close_anchor()
        self._close_heading()
        lines = (squash(l) for l in "".join(self.text).split("\n"))
        self.page.text = "\n".join(l for l in lines if l)


def parse(html, url):
    page = Page(url=url, html_length=len(html))
    p = _Parser(page)
    try:
        p.feed(html)
        p.finish()
    except Exception:
        # html.parser almost never fails, but a half-built page is better than none
        p._close_anchor()
        p._close_heading()
    return page
