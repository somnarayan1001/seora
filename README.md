# Seora

A free on-page and technical SEO checker. Paste a page address and Seora fetches the page, checks it, and gives a score out of 100 with a plain-English fix for every problem.

It runs on your own computer with nothing but Python: no third-party packages, and no outside services or APIs.

## Run it

You need Python 3.10 or newer.

```bash
python server.py
```

Then open http://localhost:8800 in your browser. Press Ctrl+C in the terminal to stop.

## What it checks

| Group | Checks | Status |
|---|---|---|
| Meta tags | Title (30–60 characters), meta description (70–160), canonical tag, robots meta tag and X-Robots-Tag (noindex, nofollow), mobile viewport, page language, character encoding, favicon, Open Graph, Twitter (X) card, JSON-LD structured data | Done |
| Content | One H1, heading order (no skipped levels, no empty headings), word count with a thin-content warning, text-to-HTML ratio | Done |
| Images | Alt text, file size, format, width and height, lazy loading | Planned |
| Links | Internal and external, broken, redirecting, nofollow, empty anchor text | Planned |
| Technical | Status code, HTTPS, mixed content, redirects, compression, caching, robots.txt, sitemap.xml | Planned |
| Page loading | Time to first byte, download time, page weight, heaviest resources | Planned |
| Whole site | Crawl with page and depth limits, Site Health score, duplicate titles and content, orphan pages | Planned |

Each problem is an **error** (costs 10 points), **warning** (4) or **notice** (1). Results can be exported as CSV.

## Settings

All settings are in `config.py`. Each can be changed with an environment variable called `SEORA_<NAME>`:

| Variable | Default | What it does |
|---|---|---|
| `SEORA_HOST` | `127.0.0.1` | Address to listen on. `127.0.0.1` means only this computer can open Seora. |
| `SEORA_PORT` | `8800` | Port to listen on |
| `SEORA_USER_AGENT` | `SeoraBot/0.1 (+https://github.com/somnarayan1001/seora)` | How Seora introduces itself to websites |
| `SEORA_TIMEOUT` | `15` | Seconds to wait for each network step |
| `SEORA_TOTAL_TIMEOUT` | `30` | Seconds allowed for one whole page fetch, redirects included |
| `SEORA_MAX_PAGE_BYTES` | `5000000` | Pages bigger than this are cut off |
| `SEORA_MAX_REDIRECTS` | `5` | Redirects followed before giving up |
| `SEORA_BLOCK_PRIVATE_ADDRESSES` | `true` | Refuse addresses on private or local networks. Keep this on. |
| `SEORA_ALLOWED_PORTS` | `80,443,8080,8443` | Ports that may be fetched |
| `SEORA_MAX_REQUEST_BYTES` | `4096` | Largest request the API accepts |
| `SEORA_MAX_CONCURRENT_AUDITS` | `4` | Checks that can run at once; others are asked to try again |

Example (PowerShell): `$env:SEORA_PORT = "9000"; python server.py`

## Safety

Seora fetches pages on behalf of whoever uses it, so it:

- only fetches `http://` and `https://` addresses on standard web ports;
- refuses addresses that lead to private or local networks (localhost, 10.x, 192.168.x, 169.254.x, IPv6 local addresses and so on), checked on every redirect, and connects only to the address it checked;
- caps the size of requests and of fetched pages (also after unzipping);
- never shows error details from the server to visitors;
- never logs the addresses people check.

## Tests

```bash
python -m unittest discover -s tests
```

Some safety tests need an internet connection for name lookups.

## API

`POST /api/audit` with `{"url": "example.com"}` returns the report as JSON. `GET /api/health` returns `{"ok": true, "version": "..."}`.
