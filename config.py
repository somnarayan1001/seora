"""All Seora settings in one place.

Every setting can be overridden with an environment variable named SEORA_<NAME>,
for example SEORA_HOST=0.0.0.0 or SEORA_PORT=9000. Nothing else in the code
should read environment variables or hard-code these values.
"""

import os


def _str(name, default):
    return os.environ.get("SEORA_" + name, default)


def _int(name, default):
    raw = os.environ.get("SEORA_" + name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Setting SEORA_{name} must be a whole number, got {raw!r}")


def _bool(name, default):
    raw = os.environ.get("SEORA_" + name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _ports(name, default):
    raw = os.environ.get("SEORA_" + name, default)
    try:
        return frozenset(int(p) for p in raw.split(",") if p.strip())
    except ValueError:
        raise SystemExit(f"Setting SEORA_{name} must be a comma-separated list of ports, got {raw!r}")


VERSION = "0.1.0"

# --- web server ---
# 127.0.0.1 keeps Seora reachable only from this PC. On a cloud server, set
# SEORA_HOST=0.0.0.0 (usually behind a reverse proxy that adds HTTPS).
HOST = _str("HOST", "127.0.0.1")
PORT = _int("PORT", 8800)
MAX_REQUEST_BYTES = _int("MAX_REQUEST_BYTES", 4096)      # largest POST body accepted
MAX_CONCURRENT_AUDITS = _int("MAX_CONCURRENT_AUDITS", 4)  # extra requests get "busy, try again"
CLIENT_TIMEOUT = _int("CLIENT_TIMEOUT", 30)               # seconds a visitor's connection may sit idle

# --- fetching other websites ---
USER_AGENT = _str("USER_AGENT", "SeoraBot/0.1 (+https://github.com/somnarayan1001/seora)")
TIMEOUT = _int("TIMEOUT", 15)                    # seconds per network step (connect, each read)
TOTAL_TIMEOUT = _int("TOTAL_TIMEOUT", 30)        # seconds for one whole fetch, redirects included
MAX_PAGE_BYTES = _int("MAX_PAGE_BYTES", 5_000_000)   # pages are cut off after this (after unzipping)
MAX_REDIRECTS = _int("MAX_REDIRECTS", 5)
# Refuse addresses that are not on the public internet (localhost, 10.x, 192.168.x,
# 169.254.x, fc00::/7, ...), checked on every redirect hop. Keep this on whenever
# anyone but you can reach the server.
BLOCK_PRIVATE_ADDRESSES = _bool("BLOCK_PRIVATE_ADDRESSES", True)
# Only these ports may be fetched, so the server can't be used to probe other services.
ALLOWED_PORTS = _ports("ALLOWED_PORTS", "80,443,8080,8443")
