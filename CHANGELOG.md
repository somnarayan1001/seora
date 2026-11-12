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
- Check group 2: Content (waiting for the owner's review of group 1).

## History

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
