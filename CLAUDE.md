# Seora: project guide

Seora is a free on-page and technical SEO checker. A small Python server fetches a page, runs checks on it, and returns a scored report with plain-English fixes. It runs on the owner's PC for now and will later move to a cloud server for other people to use, so everything must work there without a rewrite.

Read this file and `CHANGELOG.md` before changing anything. Add an entry to `CHANGELOG.md` with every change.

## Links

| What | Where |
|---|---|
| Repository (public, branch `main`) | https://github.com/somnarayan1001/seora |
| Local folder | `C:\Users\snara\Downloads\Seora` |
| Sister project whose habits this copies | Docura, `C:\Users\snara\Downloads\PDF Press` |

## Decisions already made (don't reopen them)

- **No third-party services or APIs** (no Google PageSpeed, no SERP or keyword data) and **no third-party Python packages**: Python 3.10+ standard library only. This also rules out Google Fonts on the page.
- **Scope: on-page and technical SEO only.** No keyword research, no competitor analysis.
- **Two modes:** single page (built first), then whole site (crawl with page and depth limits).
- Headless Chrome for LCP/CLS is a "maybe later": **ask the owner first.**

## Files

| File | Purpose |
|---|---|
| `server.py` | `ThreadingHTTPServer`. Serves `static/index.html`, `POST /api/audit {url}` and `GET /api/health`. |
| `config.py` | **Every setting**, each overridable by a `SEORA_<NAME>` environment variable. Nothing else reads the environment or hard-codes these values. |
| `seora/fetch.py` | Safe fetcher. **Every fetch of another website goes through `fetch()`.** |
| `seora/parse.py` | `html.parser` page model (`Page`): titles, metas, links, anchors, images, headings, scripts with inline JSON-LD, visible text, html lang. |
| `seora/checks/` | One module per check group. `__init__.py` lists them in report order in `GROUPS`. `_issue.py` has `issue()` and `clip()`. |
| `seora/audit.py` | Fetch, parse, run every group, score. `Context` is what checks receive. |
| `seora/urls.py` | URL comparison helpers. |
| `seora/robots.py` | robots.txt reader that follows Google's rules (longest match wins, `*` and `$`). Use it for the crawler too, instead of `urllib.robotparser`, which uses the first match. |
| `static/index.html` | The whole interface: HTML, CSS and JS in one file. |
| `tests/` | `unittest` tests. `test_safety.py` covers the safety rules; `test_<group>.py` covers each check group with hand-made pages. |
| `README.md` | Public description. Update its "What it checks" table when a group is added. |
| `CHANGELOG.md` | History of changes, plus open items. |
| `.claude/` | Local settings. Git-ignored. |

## Adding a check group

1. Create `seora/checks/<name>.py` with `GROUP = "Label people see"` and `run(ctx)` returning a list of `issue(...)`.
2. An issue is `{id, severity, title, detail, fix}`. Severity is `error`, `warning`, `notice` or `pass`. Ids are unique across all groups and stable (the UI and CSV use them). `fix` is a short plain-English instruction; passed checks have none.
3. `ctx` (see `audit.Context`) has `page`, `response`, `final_url`, `requested_url`, `html`, `charset`, `charset_source`, and `ctx.fetch(url, method=..., max_bytes=..., extra_headers=..., timeout=...)`, which goes through the safe fetcher and returns a `Response` or `None`. `ctx.try_fetch` returns `(Response, None)` or `(None, error code)` when the reason for a failure matters (as for broken links). Use only these for extra requests (images, links, robots.txt): they share the report's connection pool. Keep extra requests few and capped: they all count against the visitor's wait. To measure a file's size use `probe_size` in `checks/_sizes.py`: it remembers results in `ctx.memo`, so a file measured by one group (Images) isn't fetched again by another (Page loading). For many requests use `fetch_many` in `checks/_fetchmany.py` with `timeout=config.ASSET_TIMEOUT` in the probe, as `images.py` and `links.py` do: each distinct URL once, `config.PARALLEL_FETCHES` at a time, within `config.ASSET_BUDGET` seconds.
4. Add the module to `GROUPS` in `seora/checks/__init__.py`, add chips for its checks in `static/index.html` (`.chips-full`) and one for the group in `.chips-short` (shown on phones instead), and add tests in `tests/test_<name>.py` that don't need the network (replace `ctx.fetch` with a stub, as `test_meta.py` does).
5. Update `README.md`, this file if anything structural changed, and `CHANGELOG.md`.

A group that raises is caught in `audit.py` and shows "These checks couldn't finish", so one bug can't sink a report. Don't rely on that: test with odd pages.

Score: 100 minus 10 per error, 4 per warning, 1 per notice, never below 0 (`audit.PENALTY`).

## Safety rules that must always hold

Other people will reach this server later, so they must not be able to use it to scan the owner's network or anyone else's.

- **Only http and https**, only ports in `ALLOWED_PORTS`, no `user:password@` in URLs.
- **Refuse non-public addresses** (localhost, 10.x, 172.16–31.x, 192.168.x, 169.254.x, 100.64.x, IPv6 local, IPv4 hidden in IPv6, and so on) **on every redirect hop.** `resolve()` refuses a name if *any* of its addresses is private, and the connection is pinned to the address that was checked. DNS rebinding is an open item in `CHANGELOG.md`.
- **Reused connections** (`ConnectionPool`, one per report) are only ever created in `_fetch_once` right after `resolve()` approved the address, and they stay pinned to it. Never put a connection into the pool from anywhere else, and never reuse one whose reply wasn't read to the end.
- **Cap sizes:** request bodies (`MAX_REQUEST_BYTES`) and fetched pages (`MAX_PAGE_BYTES`, also after unzipping).
- **Never show raw stack traces to users.** Unexpected errors give a plain message; `log_failure` logs the exception type and code frames only, without the message (it can contain the URL).
- **Never log audited URLs.** The access log shows method, path and status only.
- **Page content is untrusted.** The interface inserts it with `textContent` only, never `innerHTML`. CSV cells starting with `= + - @` are prefixed with `'`.
- **Crawling (whole-site mode) must be polite:** delay between requests, a page cap, and respect robots.txt.
- Keep `tests/test_safety.py` passing, and add a test for any new way of fetching.

## Behaviour the owner asked for

- **Tools first, no hero.** The URL box and Check button fit the first screen at 1366×650.
- Results: score ring, Error / Warning / Notice / Passed counts (click to filter), issues grouped with their fixes, passed checks folded away per group, CSV export.
- **Plain language** in the interface and in every issue title, detail and fix: short sentences, say what to do, avoid jargon where a plain word works.
- Build check groups one at a time; **the owner reviews each group before the next one starts.** Order: Meta, Content, Images, Links, Technical, Page loading, then whole-site mode.

## Look (same as Docura)

- **Theme:** dark indigo by default, light through the moon button (remembered in `localStorage` as `seora-theme`). Colours are CSS variables on `:root` and `:root[data-theme="light"]`; define every new colour in both.
- **Motion:** add new animations to the `prefers-reduced-motion` block.
- **Phones:** check 375 px width with no sideways scrolling. Keep the 16px side gutter (`.gutter`).
- Fonts: Outfit / Figtree if installed, otherwise Segoe UI and system fonts. No font downloads.

## Testing

```bash
python -m unittest discover -s tests
```

```bash
python server.py
```

Then open http://localhost:8800 in the browser pane. Test each change against a few real sites and the refusal cases (localhost, a private IP, a non-HTML file, a bad certificate from badssl.com), at 1366×650 and 375 px, in both themes.

- The preview tool's `launch.json` lives in `.claude/` (git-ignored). If it can't start the server, run `python server.py` in the background and open the address directly.
- On Windows, a certificate "couldn't be confirmed" error for a site that opens fine in a browser is Windows not having downloaded that root yet; see `CHANGELOG.md`.
- Add `?v=N` to the address if an old page appears.

## GitHub

The CLI is signed in on this PC as `somnarayan1001`. Commit identity is set in this repository only, using the account's noreply address. Commit and push each finished change to `main`.
