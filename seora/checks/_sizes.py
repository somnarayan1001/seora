"""Measuring a file's size with as little downloading as possible, remembered for the whole report.

The Images group measures images; the Page loading group then reuses those results instead of
fetching the same files again (results are kept in ctx.memo).
"""

import re
import threading

import config

_lock = threading.Lock()


def _int(text):
    text = (text or "").strip()
    return int(text) if text.isdigit() else None


def probe_size(ctx, url, accept="*/*"):
    """(status, size in bytes, content type) for a file. status None means no answer; size None means unknown.

    Asks with HEAD first; when HEAD is refused or gives no size, asks for the first byte and reads the
    full size from Content-Range. The size is what travels over the network (compressed, if the server
    compresses), as for a browser.
    """
    key = ("size", url)
    with _lock:
        if key in ctx.memo:
            return ctx.memo[key]
    result = _probe(ctx, url, accept)
    with _lock:
        ctx.memo[key] = result
    return result


def _probe(ctx, url, accept):
    r = ctx.fetch(url, method="HEAD", accept=accept, timeout=config.ASSET_TIMEOUT)
    if r is not None and r.status < 400:
        n = _int(r.header("content-length"))
        if n:
            return r.status, n, r.content_type
    if r is not None and r.status >= 400 and r.status not in (403, 405, 501):
        return r.status, None, ""
    g = ctx.fetch(url, method="GET", accept=accept, max_bytes=config.MAX_PAGE_BYTES,
                  extra_headers={"Range": "bytes=0-0"}, timeout=config.ASSET_TIMEOUT)
    if g is None:
        return (r.status if r is not None else None), None, ""
    if g.status >= 400:
        return g.status, None, ""
    if g.status == 206:
        m = re.search(r"/\s*(\d+)\s*$", g.header("content-range"))
        n = int(m.group(1)) if m else None
    else:  # the server ignored Range and sent the whole file
        n = _int(g.header("content-length")) or (None if g.truncated else g.transfer_bytes or len(g.body))
    return g.status, n, g.content_type
