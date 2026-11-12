"""Safe fetcher for other websites.

Rules this module guarantees (see CLAUDE.md, "Safety rules"):
- Only http and https URLs, only the ports in config.ALLOWED_PORTS, no user:password@ in URLs.
- Every hostname is resolved here, and every address it resolves to must be public
  (when config.BLOCK_PRIVATE_ADDRESSES is on). The connection then goes to the address
  that was checked, so a second DNS lookup can't swap in a private one.
- Redirects are followed by hand, and each hop goes through the same checks.
- Bodies are capped at config.MAX_PAGE_BYTES, also after unzipping.

Everything that fetches another website must go through fetch().
"""

import http.client
import ipaddress
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import config

REDIRECT_CODES = {301, 302, 303, 307, 308}
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL_LENGTH = 2048


class FetchError(Exception):
    """A problem worth telling the user about. The message is plain English and safe to show."""

    def __init__(self, message, code="fetch_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Hop:
    url: str
    status: int
    location: str = ""


@dataclass
class Response:
    url: str                      # final URL, after redirects
    status: int
    reason: str
    headers: list                 # [(name, value), ...] as sent, for headers that repeat
    body: bytes                   # decoded (unzipped) body, capped
    chain: list = field(default_factory=list)  # [Hop, ...] for each redirect, final hop not included
    content_encoding: str = ""    # e.g. "gzip", "" when sent uncompressed
    transfer_bytes: int = 0       # bytes received over the wire for the body (compressed size)
    truncated: bool = False       # body was cut off at the size cap
    ttfb_ms: float = 0.0          # final hop: from starting the request to the first byte of the reply
    download_ms: float = 0.0      # final hop: time spent receiving the body
    total_ms: float = 0.0         # all hops, DNS and connection setup included
    ip: str = ""                  # address of the final server

    def header(self, name, default=""):
        """First value of a header (case-insensitive)."""
        name = name.lower()
        for k, v in self.headers:
            if k.lower() == name:
                return v
        return default

    def header_all(self, name):
        name = name.lower()
        return [v for k, v in self.headers if k.lower() == name]

    @property
    def content_type(self):
        return self.header("content-type").split(";")[0].strip().lower()

    @property
    def is_html(self):
        ct = self.content_type
        return ct in ("text/html", "application/xhtml+xml") or (ct == "" and b"<html" in self.body[:2048].lower())


# ---------- URL checks ----------

def normalise_input(raw):
    """Turn what a person typed into a full URL, or raise FetchError."""
    if not isinstance(raw, str):
        raise FetchError("Please enter a web address.", "bad_url")
    url = raw.strip()
    if not url:
        raise FetchError("Please enter a web address.", "bad_url")
    if len(url) > MAX_URL_LENGTH:
        raise FetchError("That web address is too long.", "bad_url")
    if "://" not in url:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url) and not re.match(r"^[^:/]+:\d+", url):
            raise FetchError("Only web addresses starting with http:// or https:// can be checked.", "bad_scheme")
        url = "https://" + url
    return check_url(url)


def check_url(url):
    """Validate an absolute URL and return it cleaned (fragment removed, host in punycode)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchError("That doesn't look like a valid web address.", "bad_url")
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise FetchError("Only web addresses starting with http:// or https:// can be checked.", "bad_scheme")
    if parts.username is not None or parts.password is not None:
        raise FetchError("Web addresses with a user name or password in them can't be checked.", "bad_url")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise FetchError("That web address has no site name in it.", "bad_url")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise FetchError("That site name isn't valid.", "bad_url")
    port = port or DEFAULT_PORTS[scheme]
    if port not in config.ALLOWED_PORTS:
        raise FetchError(f"Only standard web ports can be checked (port {port} isn't allowed).", "bad_port")
    netloc = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    path = parts.path or "/"
    # Encode spaces, control characters and non-ASCII; keep existing %xx escapes as they are
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/%:@!$&'()*+,;=-._~?")
    return urlunsplit((scheme, netloc, path, query, ""))


def is_public_ip(ip):
    addr = ipaddress.ip_address(ip)
    if addr.version == 6:
        # ::ffff:10.0.0.1 and the like are IPv4 addresses in disguise
        if addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        elif addr.sixtofour:
            addr = addr.sixtofour
        elif addr.teredo:
            return False
    return bool(
        addr.is_global
        and not addr.is_multicast
        and not addr.is_reserved
        and not addr.is_loopback
        and not addr.is_link_local
        and not addr.is_private
        and not addr.is_unspecified
    )


def resolve(host, port):
    """Resolve a host and return one address to connect to. Refuses non-public addresses."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("That site couldn't be found. Check the address for typos.", "dns")
    except UnicodeError:
        raise FetchError("That site name isn't valid.", "bad_url")
    addrs = []
    for family, _, _, _, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            ip = sockaddr[0].split("%")[0]
            if ip not in addrs:
                addrs.append(ip)
    if not addrs:
        raise FetchError("That site couldn't be found. Check the address for typos.", "dns")
    if config.BLOCK_PRIVATE_ADDRESSES:
        # Refuse if any address is private, not just the first: a name that mixes
        # public and private addresses is a classic way to reach internal machines.
        if any(not is_public_ip(ip) for ip in addrs):
            raise FetchError("That address points to a private or local network, so it can't be checked.", "blocked")
    # Prefer IPv4, which works on more networks
    addrs.sort(key=lambda ip: ":" in ip)
    return addrs[0]


# ---------- connections pinned to a checked address ----------

class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


_TLS = ssl.create_default_context()


# ---------- fetching ----------

def fetch(url, method="GET", max_bytes=None, accept="text/html,application/xhtml+xml,*/*;q=0.8",
          extra_headers=None, follow_redirects=True):
    """Fetch a URL safely. Returns a Response, or raises FetchError with a plain-English message."""
    max_bytes = config.MAX_PAGE_BYTES if max_bytes is None else max_bytes
    started = time.monotonic()
    deadline = started + config.TOTAL_TIMEOUT
    url = check_url(url)
    chain = []
    seen = set()
    while True:
        if url in seen:
            raise FetchError("The page redirects in a loop, so it never loads.", "redirect_loop")
        seen.add(url)
        resp = _fetch_once(url, method, max_bytes, accept, extra_headers or {}, deadline)
        location = resp.header("location")
        if follow_redirects and resp.status in REDIRECT_CODES and location:
            chain.append(Hop(url, resp.status, location))
            if len(chain) > config.MAX_REDIRECTS:
                raise FetchError(f"The page redirects more than {config.MAX_REDIRECTS} times, so it was not followed further.", "too_many_redirects")
            try:
                url = check_url(urljoin(url, location.strip()))
            except FetchError as e:
                raise FetchError("The page redirects to an address that can't be checked. " + e.message, e.code)
            if resp.status == 303 and method != "HEAD":
                method = "GET"
            continue
        resp.chain = chain
        resp.total_ms = (time.monotonic() - started) * 1000
        return resp


def _fetch_once(url, method, max_bytes, accept, extra_headers, deadline):
    parts = urlsplit(url)
    host = parts.hostname
    port = parts.port or DEFAULT_PORTS[parts.scheme]
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    hop_start = time.monotonic()
    timeout = max(1.0, min(config.TIMEOUT, deadline - hop_start))
    ip = resolve(host, port)
    if parts.scheme == "https":
        conn = _PinnedHTTPS(host, ip, port, timeout, _TLS)
    else:
        conn = _PinnedHTTP(host, ip, port, timeout)
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en;q=0.9, *;q=0.5",
        "Connection": "close",
    }
    headers.update(extra_headers)
    try:
        conn.request(method, target, headers=headers)
        r = conn.getresponse()
        ttfb = (time.monotonic() - hop_start) * 1000
        raw_headers = list(r.getheaders())
        body, transfer, truncated, encoding = b"", 0, False, ""
        dl_start = time.monotonic()
        if method != "HEAD" and r.status not in REDIRECT_CODES:
            body, transfer, truncated, encoding = _read_body(r, max_bytes, deadline)
        download = (time.monotonic() - dl_start) * 1000
        return Response(url=url, status=r.status, reason=r.reason or "", headers=raw_headers, body=body,
                        content_encoding=encoding, transfer_bytes=transfer, truncated=truncated,
                        ttfb_ms=ttfb, download_ms=download, ip=ip)
    except FetchError:
        raise
    except ssl.SSLCertVerificationError as e:
        raise FetchError(_cert_message(getattr(e, "verify_code", 0)), "tls_cert")
    except ssl.SSLError:
        raise FetchError("A secure (HTTPS) connection to the site couldn't be made.", "tls")
    except (socket.timeout, TimeoutError):
        raise FetchError("The site took too long to answer.", "timeout")
    except ConnectionRefusedError:
        raise FetchError("The site refused the connection.", "refused")
    except (ConnectionError, OSError):
        raise FetchError("The site couldn't be reached.", "unreachable")
    except http.client.HTTPException:
        raise FetchError("The site sent a reply that couldn't be understood.", "bad_reply")
    finally:
        conn.close()


def _cert_message(code):
    """Plain-English reason for a certificate failure (OpenSSL verify codes)."""
    if code == 10:
        return "The site's security certificate has expired, so browsers show a warning instead of the page."
    if code == 62:
        return "The site's security certificate is for a different site name, so browsers show a warning instead of the page."
    if code in (18, 19):
        return "The site uses a self-signed security certificate, so browsers show a warning instead of the page."
    if code == 20:
        # Also happens on Windows when its certificate store hasn't downloaded a newer root yet
        return ("The site's security certificate couldn't be confirmed: the authority that issued it isn't known to this server. "
                "If the site opens normally in your browser, try again in a minute.")
    return "The site's security certificate isn't valid, so browsers would show a warning."


def _read_body(r, max_bytes, deadline):
    """Read and unzip a body, stopping at max_bytes of output. Returns (body, wire_bytes, truncated, encoding)."""
    encoding = (r.getheader("content-encoding") or "").strip().lower()
    if encoding in ("gzip", "x-gzip"):
        dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        dec = None  # decided on the first chunk: zlib-wrapped or raw
    elif encoding in ("", "identity"):
        dec = False
    else:
        # br, zstd...: we never ask for these, so a server that sends them anyway
        # gets its raw bytes measured but not parsed
        dec = False
    out = bytearray()
    wire = 0
    truncated = False
    while True:
        if time.monotonic() > deadline:
            raise FetchError("The site took too long to send the page.", "timeout")
        chunk = r.read(65536)
        if not chunk:
            break
        wire += len(chunk)
        if dec is None:
            dec = zlib.decompressobj(zlib.MAX_WBITS if (chunk[0] & 0x0F) == 8 else -zlib.MAX_WBITS)
        if dec is False:
            out += chunk
        else:
            try:
                out += dec.decompress(chunk, max_bytes + 1 - len(out))
            except zlib.error:
                raise FetchError("The site sent a damaged compressed page.", "bad_reply")
        if len(out) > max_bytes:
            truncated = True
            del out[max_bytes:]
            break
    return bytes(out), wire, truncated, encoding


# ---------- turning bytes into text ----------

META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-:.]+)""", re.I)


def decode_html(resp):
    """Return (text, charset, where_it_came_from). where is 'header', 'meta' or 'guess'."""
    charset, source = "", "guess"
    m = re.search(r"charset\s*=\s*[\"']?([^\s;\"']+)", resp.header("content-type"), re.I)
    if m:
        charset, source = m.group(1), "header"
    else:
        m = META_CHARSET.search(resp.body[:4096])
        if m:
            charset, source = m.group(1).decode("ascii", "replace"), "meta"
    if resp.body.startswith(b"\xef\xbb\xbf"):
        charset = charset or "utf-8"
    try:
        text = resp.body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        text = resp.body.decode("utf-8", errors="replace")
    return text.lstrip("﻿"), charset.lower(), source
