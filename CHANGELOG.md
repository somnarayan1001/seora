# Seora change log

Newest first. Add an entry with every change, and keep "Open items" current.

## Open items

**Security, before other people use it**
- **DNS rebinding.** Seora resolves each host name itself, refuses it if any address is private, and then connects to the exact address it checked (on every redirect hop), so a second lookup can't swap in a private address. Still open: this relies on every fetch going through `seora/fetch.py`; any future code that fetches some other way (for example headless Chrome for LCP/CLS) would bypass it and needs its own guard, such as an outbound firewall rule on the cloud server that blocks private ranges.
- No per-visitor rate limit yet, only a cap of 4 checks at once (`MAX_CONCURRENT_AUDITS`). Needed before the cloud move, together with HTTPS through a reverse proxy.

**Not yet tested**
- A real phone (only a simulated 375 px screen so far).
- Running on Linux or a cloud server. Only run on Windows with Python 3.13 so far; the code uses nothing newer than Python 3.10.

**Known small items**
- On Windows, Python only trusts root certificates that Windows has already downloaded. A site whose certificate chains to a root this PC hasn't seen yet fails with "couldn't be confirmed" until Windows fetches that root (opening the site once in Edge or Chrome does it). Linux servers have a full certificate bundle, so this goes away after the cloud move.
- Fonts: the Docura fonts (Outfit, Figtree) come from Google Fonts, which would be an outside service, so Seora uses them only if installed and otherwise falls back to Segoe UI / system fonts.
- Some sites answer bots slowly or not at all (neverssl.com takes about 16 s for any non-browser user agent, so Seora times out at 15 s). That is what search engine bots see too, so Seora reports it rather than pretending to be a browser.
- Brotli (`br`) compression can't be decoded with the standard library, so Seora asks for gzip only. The Technical group will check for brotli support separately.

**Next**
- Check group 3: Images (waiting for the owner's review of group 2).

## History

### 2026-11-12: Several H1s is now an error
- Owner's decision after reviewing the Content group: a page must have exactly one H1, so more than one is an error (10 points) instead of a warning (4). Skipped heading levels, the 300-word thin-content limit and the 10% text ratio stay as they were.

### 2026-11-12: Check group 2: Content
- New group `seora/checks/content.py`, shown after Meta tags.
- **H1:** missing or empty is an error; more than one is a warning; one H1 passes and shows its text. An image inside a heading counts with its alt text, as search engines read it, so a logo H1 isn't reported as empty.
- **Heading order:** a jump of more than one level down (H1 → H3) is a notice, listing the first three places. Going back up (H4 → H2) is fine. A first heading of H3 counts as a skip. Empty headings are a notice. A page with no headings at all is a warning.
- **Word count:** fewer than 300 words is a warning for thin content. Menus and footers are included in the count, which the report says. When a page has under 50 words and loads scripts, the detail explains that the text is probably added by JavaScript, which Seora doesn't run. Chinese and Japanese text is counted at about two characters per word, since those languages don't use spaces.
- **Text-to-HTML ratio:** under 10% is a notice (a weak signal on its own, but often a sign of a heavy page).
- Severities chosen for the owner's review: skipped heading levels and a low text ratio are notices, thin content and several H1s are warnings.
- Page reading fixes found while testing real sites:
  - Headings that hold only an SVG logo are named by its `aria-label` or SVG `<title>` (bbc.com/news has `<h1><svg aria-label="News">`, which was reported as an empty H1). Real text in a heading still wins over a label, and a name given twice is shown once.
  - Text inside `<textarea>` is no longer counted as page text. baidu.com keeps hidden HTML templates in textareas, which made it look like 41,909 words of content.
- 12 new tests in `tests/test_content.py` (34 in total).
- Checked with the server running: Wikipedia article, github.com, gov.uk, bbc.com/news, MDN, yahoo.co.jp (17 H1s, thin content), react.dev (2 H1s, H2 → H4), example.com (now has no headings at all). Baidu was checked before the textarea fix and then started refusing connections after repeated requests, so the fix is covered by a test instead. Interface checked at 1366×650 (the chips now take two rows and still fit the first screen) and at 375 px with no sideways scrolling, in both themes.

### 2026-11-12: First version: skeleton and Meta checks
- New project, following the habits of Docura: project guide in `CLAUDE.md`, this change log, plain-language interface, Docura's dark indigo look with a light theme on the moon button.
- `server.py`: standard-library web server on http://localhost:8800. Serves the page and `POST /api/audit`. Never logs audited addresses (only method, path and status), and never shows stack traces to visitors.
- `config.py`: every setting in one place, each overridable with a `SEORA_*` environment variable, so the cloud move needs only different settings.
- `seora/fetch.py`: safe fetcher. Only http/https on ports 80, 443, 8080, 8443; no `user:password@`; every address checked against private and local ranges (including IPv4 hidden inside IPv6), on every redirect hop; connection pinned to the checked address; body capped at 5 MB after unzipping; 15 s per step and 30 s overall. Records the redirect chain, status, headers, time to first byte, total time and size. Certificate problems are explained in plain words (expired, wrong name, self-signed).
- `seora/parse.py`: page model with title, metas, links, anchors, images, headings, scripts (with inline JSON-LD), visible text and page language. Survives broken HTML such as a missing `</head>`.
- `seora/checks/meta.py`, group 1, **Meta tags**: title (30–60 characters), meta description (70–160), canonical (missing, conflicting, empty, relative, http instead of https, trailing-slash mismatch, points elsewhere; HTTP Link header too), robots meta and X-Robots-Tag (noindex, nofollow, none, nosnippet; rules for other bots ignored), viewport (missing, no device width, zoom blocked), html lang, charset (missing, not UTF-8, header and page disagree, declared too late), favicon (link tag checked for loading; falls back to /favicon.ico), Open Graph, Twitter (X) card, JSON-LD (valid JSON, @context, types found).
- Score: 100 minus 10 per error, 4 per warning, 1 per notice.
- Interface: URL box and Check button on the first screen at 1366×650; score ring, Error / Warning / Notice / Passed counts that filter the list, page facts (status, first byte, load time, HTML size), issues grouped with "How to fix", passed checks folded away, CSV export (cells starting with = + - @ are made safe for spreadsheets). "Whole site" shows as coming soon.
- Tests (`python -m unittest discover -s tests`, 22 tests): blocked schemes, ports and credentials; private IPv4/IPv6 addresses, numeric forms like `2130706433`, public names that point inside (localtest.me, nip.io); a redirect to 127.0.0.1 refused at the second hop; redirect limit; gzip bomb capped; Meta checks on hand-made pages.
- Checked against real sites with the server running: python.org, wikipedia.org, a Wikipedia article, github.com (and http:// redirect), amazon.in, developer.mozilla.org, gov.uk, bbc.com/news, example.com. Refusals checked: localhost, 192.168.1.1, 169.254.169.254, ftp://, unknown domain, a PNG, expired, self-signed and wrong-name certificates, oversized and malformed requests. Interface checked at 1366×650 and 375 px (no sideways scrolling), in both themes.
