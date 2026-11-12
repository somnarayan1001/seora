"""Single-page audit: fetch, parse, run every check group, score."""

import logging
import time
import traceback
from dataclasses import dataclass, field

import config

from . import fetch as fetchmod
from .checks import GROUPS
from .checks._issue import issue
from .fetch import FetchError
from .parse import parse

log = logging.getLogger("seora")

PENALTY = {"error": 10, "warning": 4, "notice": 1, "pass": 0}


class AuditError(Exception):
    """The page can't be audited. The message is plain English and safe to show."""

    def __init__(self, message, code="audit_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Context:
    """What every check receives."""
    requested_url: str
    final_url: str
    response: fetchmod.Response
    page: object
    html: str
    charset: str
    charset_source: str
    extra_fetches: list = field(default_factory=list)
    pool: object = None  # fetchmod.ConnectionPool shared by every fetch for this report

    def fetch(self, url, **kw):
        """Fetch another resource (image, favicon, robots.txt...) through the safe fetcher.
        Returns a Response, or None when it couldn't be fetched for any reason."""
        return self.try_fetch(url, **kw)[0]

    def try_fetch(self, url, **kw):
        """Like fetch(), but returns (Response, None) or (None, FetchError code such as "dns",
        "timeout", "blocked", "tls_cert"), for checks that need to know why a fetch failed."""
        self.extra_fetches.append(url)
        kw.setdefault("pool", self.pool)
        try:
            return fetchmod.fetch(url, **kw), None
        except FetchError as e:
            return None, e.code


def log_failure(where, exc):
    """Log an internal error without the audited URL or exception message (they can contain it)."""
    frames = "".join(traceback.format_list(traceback.extract_tb(exc.__traceback__)))
    log.error("%s failed: %s\n%s", where, type(exc).__name__, frames)


def score_of(issues):
    return max(0, 100 - sum(PENALTY[i["severity"]] for i in issues))


def audit(raw_url):
    pool = fetchmod.ConnectionPool()
    try:
        return _audit(raw_url, pool)
    finally:
        pool.close()


def _audit(raw_url, pool):
    started = time.monotonic()
    try:
        url = fetchmod.normalise_input(raw_url)
        resp = fetchmod.fetch(url, pool=pool)
    except FetchError as e:
        raise AuditError(e.message, e.code)
    if not resp.is_html:
        ct = resp.content_type or "unknown"
        raise AuditError(f"That address isn't a web page (the server sent {ct}), so it can't be checked.", "not_html")
    html, charset, source = fetchmod.decode_html(resp)
    page = parse(html, resp.url)
    ctx = Context(requested_url=url, final_url=resp.url, response=resp, page=page, html=html,
                  charset=charset, charset_source=source, pool=pool)

    groups = []
    for mod in GROUPS:
        try:
            issues = mod.run(ctx)
        except Exception as e:  # one broken check must not sink the whole report
            log_failure(f"check group {mod.__name__}", e)
            issues = [issue(f"{mod.__name__.rsplit('.', 1)[-1]}-failed", "notice", "These checks couldn't finish",
                            "Something went wrong on our side while checking this part of the page.",
                            "Try again later. If it keeps happening, report it with the page address.")]
        groups.append({"group": mod.GROUP, "id": mod.__name__.rsplit(".", 1)[-1], "issues": issues})

    every = [i for g in groups for i in g["issues"]]
    counts = {s: sum(1 for i in every if i["severity"] == s) for s in PENALTY}
    notes = []
    if resp.status >= 400:
        notes.append(f"The server answered with status {resp.status}, so search engines won't list this page. The checks below look at what it sent anyway.")
    if resp.truncated:
        notes.append(f"The page is larger than {config.MAX_PAGE_BYTES // 1_000_000} MB, so only the first part was checked.")
    return {
        "url": url,
        "final_url": resp.url,
        "status": resp.status,
        "redirects": [{"url": h.url, "status": h.status} for h in resp.chain],
        "score": score_of(every),
        "counts": counts,
        "groups": groups,
        "notes": notes,
        "page": {
            "title": page.title,
            "ttfb_ms": round(resp.ttfb_ms),
            "total_ms": round(resp.total_ms),
            "html_bytes": len(resp.body),
            "transfer_bytes": resp.transfer_bytes,
            "compression": resp.content_encoding or "none",
        },
        "checked_in_ms": round((time.monotonic() - started) * 1000),
        "version": config.VERSION,
    }
