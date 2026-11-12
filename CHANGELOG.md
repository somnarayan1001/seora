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

**Speed**
- A full report now takes about 8–13 s for big pages (bbc.com/news, github.com, gov.uk, python.org) and under a second for tiny ones. Sites with slow side pages can reach the time limits: apple.com takes about 33 s, because its shop links each go through two redirects (about 4.5 s each), so the Links group stops at its 25 s limit and reports the rest as not checked. Running the Images and Links groups at the same time would save up to 25 s on such pages; not done, to keep the load on each site at 6 requests at once.

**Next**
- Check group 5: Technical (waiting for the owner's review of group 4).

## History

### 2026-11-12: Check group 4: Links, and connection reuse
- New group `seora/checks/links.py`, shown after Images.
- **Count:** links to this site and to other sites (www. and no www. count as the same site), and how many different addresses they go to. A page with no links to its own site is a warning; a page with no links at all is a notice. Same-page jumps (`#section`), `javascript:` links and email/phone links aren't counted as links.
- **Broken links:** each distinct address is checked once, links to this site first: a HEAD request, and a small GET when HEAD fails or is refused (some servers answer HEAD wrongly). 4xx and 5xx answers, a site that doesn't exist, a certificate problem or a refused connection are an error, listing up to 5. A redirect that ends on a missing page is broken too. 401, 403, 429 and 999 (LinkedIn) are a notice ("refused Seora's check"), because sites with bot protection turn away automated checks. No answer in time is a notice. Up to 80 addresses per page (`MAX_LINKS_CHECKED`), within the same 25 s budget as images.
- **Redirecting links:** a notice listing "old → new (status)", saying how many go to this site.
- **nofollow:** links to your own site marked nofollow, ugc or sponsored are a warning. On links to other sites it's fine and only counted.
- **Links without text:** a warning. Image alt text, `aria-label` (on the link or an icon inside it), an SVG `<title>` and the `title` attribute all count as text, so icon links for social sites aren't reported. Link copies inside `<noscript>` aren't counted.
- **Connection reuse:** all fetches for one report now share a pool of kept-alive connections (`ConnectionPool` in `fetch.py`), as browsers do. This roughly halved report times: bbc.com/news 38 s → 13 s, gov.uk 24 s → 9 s, github.com 22 s → 10 s, python.org 15 s → 9 s. It's also gentler on sites. Safety is unchanged: a connection only enters the pool after its address passed the private-address check, it stays connected to that address, a connection whose reply wasn't read to the end is never reused, and the pool is closed when the report is done.
- The image and link checks now share one helper (`checks/_fetchmany.py`) for fetching many addresses politely within the time budget. Checks that need to know why a fetch failed use the new `ctx.try_fetch`.
- The waiting message now says "Measuring images and checking links…".
- 18 new tests (65 in total): `tests/test_links.py` with a fake web (missing, moved and bot-blocking pages), and `tests/test_pool.py` against a real local server (one connection reused for 5 requests, servers that close connections, cut-off replies not reused, the private-address block still applies).
- Checked with the server running: python.org, gov.uk, react.dev, a Wikipedia article (551 links, so the 80 cap shows), github.com, bbc.com/news, example.com (no links), w3.org/TR (4,215 links), apple.com. Interface checked at 1366×650 (the list of checks is now 3 rows and still fits the first screen) and 375 px with no sideways scrolling.
- Severities chosen for the owner's review: broken links are an error; no links to your own site, nofollow on your own links and links without text are warnings; redirecting links, refused checks and links that didn't answer are notices.

### 2026-11-12: Check group 3: Images
- New group `seora/checks/images.py`, shown after Content.
- **Alt text:** images with no alt attribute are a warning, listing up to 5 by file name. Empty alt (`alt=""`) is a notice, because it's right for decorative images but wrong for images that show content. Images marked decorative (`role="presentation"`, `aria-hidden="true"`) or described with `aria-label` aren't counted as missing (bbc.com uses `aria-label="image unavailable"` on its placeholders).
- **Broken images:** any image answering 4xx or 5xx is an error. Images that don't answer at all are a notice ("couldn't be checked"), not counted as broken.
- **File size:** each distinct image is fetched once: a HEAD request, or a 1-byte ranged GET when HEAD is refused or gives no size (the size is read from Content-Range). Over 200 KB is a warning, listing the heaviest first, with the total for all images. When the large images offer smaller versions through srcset, the report says that browsers on small screens may download less.
- **Format:** JPEG, PNG, GIF, BMP or TIFF files over 10 KB are a notice suggesting WebP or AVIF. Images inside `<picture>` with a WebP or AVIF `<source>` are fine. Images are requested with the same Accept header as a browser, so servers that pick the format per visitor (such as BBC's) are judged on what visitors get.
- **Width and height:** images missing either attribute are a notice (layout shift). It's not a warning because Seora can't see CSS, which may already reserve the space.
- **Lazy loading:** after the first 3 images, those without `loading="lazy"` are a notice. A lazy first or second image is a notice that says it only matters if the image is on the first screen (gov.uk's first images are further down). Script-based lazy loading (`data-src` with a placeholder `src`) counts as lazy, and the real address is the one measured.
- Tracking pixels (1×1, 0×0) are left out of every check. Fallback copies inside `<noscript>` aren't counted twice.
- Limits: up to 40 images fetched per page, 6 at a time, 8 s per image and 25 s for the whole group. Anything past the limits is reported as not checked. All of this goes through the safe fetcher, so the private-address rules apply to every image too. The fetcher gained a `timeout` option for these shorter side requests.
- The waiting message now says "Measuring images…" after about 5 seconds.
- 13 new tests in `tests/test_images.py` (47 in total), with a fake server that imitates the three ways servers answer (size in HEAD, HEAD refused but Range honoured, Range ignored).
- Checked with the server running: bbc.com/news, Wikipedia article, gov.uk, react.dev, amazon.in, unsplash.com (18 images over 200 KB), apple.com (over 40 images, so the cap shows), python.org. Interface checked at 1366×650 (the list of checks is now 2 rows and still fits) and 375 px with no sideways scrolling.
- Severities chosen for the owner's review: missing alt is a warning, broken images an error, large images a warning; empty alt, format, width/height and lazy loading are notices.

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
