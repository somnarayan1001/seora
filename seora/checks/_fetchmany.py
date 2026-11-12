"""Fetching many side resources (images, links...) for one report, politely and within a time budget."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

import config


def fetch_many(ctx, urls, probe, cap):
    """Run probe(ctx, url) for each distinct URL, config.PARALLEL_FETCHES at a time.

    Only the first `cap` URLs are probed, and waiting stops after config.ASSET_BUDGET seconds.
    Returns (results, attempted, skipped): results maps URL -> probe result for those that
    finished (a probe that raised gives None), attempted is the set of URLs that were started,
    and skipped is how many were left out because of the cap.
    """
    urls = list(dict.fromkeys(urls))
    todo, skipped = urls[:cap], max(0, len(urls) - cap)
    results = {}
    if not todo:
        return results, set(), skipped
    pool = ThreadPoolExecutor(max_workers=max(1, config.PARALLEL_FETCHES))
    try:
        futures = {pool.submit(probe, ctx, u): u for u in todo}
        try:
            for f in as_completed(futures, timeout=config.ASSET_BUDGET):
                try:
                    results[futures[f]] = f.result()
                except Exception:
                    results[futures[f]] = None
        except FuturesTimeout:
            pass  # still running: reported as "couldn't be checked"
    finally:
        # Requests already running finish on their own within config.ASSET_TIMEOUT
        pool.shutdown(wait=False, cancel_futures=True)
    return results, set(todo), skipped
