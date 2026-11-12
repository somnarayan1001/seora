"""Content: the H1, heading order, amount of text and text-to-HTML ratio."""

from ._issue import clip, issue

GROUP = "Content"

THIN_WORDS = 300          # fewer words than this is thin content
LOW_TEXT_RATIO = 0.10     # visible text under 10% of the HTML is low


def run(ctx):
    return h1(ctx) + heading_order(ctx) + word_count(ctx) + text_ratio(ctx)


# ---------- H1 ----------

def h1(ctx):
    h1s = [text for level, text in ctx.page.headings if level == 1]
    if not h1s:
        return [issue("h1-missing", "error", "H1 heading is missing",
                      "Every page should have one main heading (<h1>) that says what it is about. Search engines and screen readers use it to understand the page.",
                      "Add one <h1> near the top of the page with the page's main topic, in words close to the title.")]
    out = []
    if len(h1s) > 1:
        shown = "; ".join(f"“{clip(t, 50)}”" if t else "(empty)" for t in h1s[:4]) + ("; …" if len(h1s) > 4 else "")
        out.append(issue("h1-multiple", "error", f"Page has {len(h1s)} H1 headings",
                         f"{shown}. With several main headings it's less clear what the page is mainly about.",
                         "Keep one <h1> for the main topic and change the others to <h2>."))
    if not h1s[0]:
        out.append(issue("h1-empty", "error", "H1 heading is empty",
                         "The <h1> has no text in it, and no image alt text or label that could stand in for it.",
                         "Put the page's main topic in the <h1> as text."))
    elif len(h1s) == 1:
        out.append(issue("h1-ok", "pass", "Page has one H1 heading", f"“{clip(h1s[0])}”"))
    return out


# ---------- heading order ----------

def heading_order(ctx):
    heads = ctx.page.headings
    if not heads:
        return [issue("headings-missing", "warning", "Page has no headings",
                      "Headings (<h1> to <h6>) split a page into sections. Without them, search engines and screen-reader users can't see its structure.",
                      "Use one <h1> for the page topic, <h2> for each main section and <h3> for parts of a section.")]
    out = []
    skips = []
    prev = 1  # the page itself counts as level 1, so a first heading of <h3> skips <h2>
    for level, text in heads:
        if level > prev + 1:
            skips.append((prev, level, text))
        prev = level
    if skips:
        examples = "; ".join(f"H{a} → H{b} at “{clip(t, 40)}”" if t else f"H{a} → H{b}" for a, b, t in skips[:3])
        more = f" and {len(skips) - 3} more" if len(skips) > 3 else ""
        out.append(issue("headings-skipped", "notice",
                         f"Heading levels are skipped {len(skips)} time{'s' if len(skips) != 1 else ''}",
                         f"{examples}{more}. Skipping levels makes the page's outline harder to follow, especially for screen-reader users.",
                         "Go down one level at a time: <h2> after <h1>, <h3> after <h2>. Pick headings by structure, and change their size with CSS."))
    empty = sum(1 for _, t in heads if not t)
    if empty:
        out.append(issue("headings-empty", "notice", f"{empty} empty heading{'s' if empty != 1 else ''}",
                         "Some heading tags have no text. They are often used for spacing or icons.",
                         "Remove empty heading tags, or put text in them. Use CSS for spacing."))
    if not out:
        counts = {}
        for level, _ in heads:
            counts[level] = counts.get(level, 0) + 1
        summary = ", ".join(f"{n} × H{l}" for l, n in sorted(counts.items()))
        out.append(issue("headings-ok", "pass", "Heading levels are in order", summary))
    return out


# ---------- amount of text ----------

def word_count(ctx):
    words = ctx.page.word_count
    if words < THIN_WORDS:
        js = sum(1 for s in ctx.page.scripts if s.get("src"))
        hint = ""
        if words < 50 and js:
            hint = (" The text may be added by JavaScript after the page loads. Seora reads the page as sent by the server, "
                    "like most search engine crawlers do on their first pass, so text added later is not counted.")
        return [issue("content-thin", "warning", f"Thin content: {words} word{'s' if words != 1 else ''}",
                      f"Pages with fewer than {THIN_WORDS} words often rank poorly because there is little for search engines to match. "
                      f"Menus and footers are included in this count.{hint}",
                      "Add useful text that answers what visitors come for: details, examples, questions people ask. "
                      "If the text is added by JavaScript, also send it in the page's HTML.")]
    return [issue("content-ok", "pass", f"Page has {words:,} words", "Menus and footers are included in this count.")]


def text_ratio(ctx):
    html_len = ctx.page.html_length
    if not html_len:
        return []
    text_len = len(ctx.page.text)
    ratio = text_len / html_len
    pct = f"{ratio * 100:.1f}%"
    detail = f"{text_len:,} characters of visible text in {html_len:,} characters of HTML."
    if ratio < LOW_TEXT_RATIO:
        return [issue("text-ratio-low", "notice", f"Low text-to-HTML ratio ({pct})",
                      detail + " Most of the page is code (scripts, styles, markup) rather than text. On its own this rarely hurts rankings, but it often means a heavy, slow page.",
                      "Move inline scripts and styles into separate files, remove unused markup, and make sure the main content is in the HTML as text.")]
    return [issue("text-ratio-ok", "pass", f"Text-to-HTML ratio is {pct}", detail)]
