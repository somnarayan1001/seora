"""URL helpers shared by checks."""

from urllib.parse import urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def same_page(a, b):
    """True when two absolute URLs point at the same page (case of scheme/host, default port and fragment ignored)."""
    return comparable(a) == comparable(b)


def comparable(url):
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return url
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower().rstrip(".")
    netloc = host if not port or port == DEFAULT_PORTS.get(scheme) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, p.path or "/", p.query, ""))


def host_of(url):
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_absolute_http(url):
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    return p.scheme.lower() in ("http", "https") and bool(p.hostname)
