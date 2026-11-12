"""Seora web server. Run with:  python server.py

Serves the interface (static/index.html) and the API:
  POST /api/audit  {"url": "..."}  ->  JSON report (see seora/audit.py)
  GET  /api/health                 ->  {"ok": true, "version": "..."}

Privacy: audited URLs are never logged. The access log shows method, path and status only.
"""

import json
import logging
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config
from seora.audit import AuditError, audit, log_failure

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "static" / "index.html"
log = logging.getLogger("seora")
audit_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_AUDITS)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "Seora/" + config.VERSION
    sys_version = ""
    timeout = config.CLIENT_TIMEOUT
    protocol_version = "HTTP/1.1"

    # ---- logging: never the query string or request body ----
    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        path = (self.path or "").split("?", 1)[0]
        log.info("%s %s %s", self.command, path[:100], code)

    def log_error(self, fmt, *args):
        # Idle keep-alive connections timing out are normal; anything else is a malformed request.
        # Only the kind of problem is logged, never the request line (it can hold a URL).
        if not fmt.startswith("Request timed out"):
            log.warning("bad request from client (%s)", fmt.split(" ")[0])

    # ---- responses ----
    def _send(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, status, message, code):
        self._json(status, {"error": message, "code": code})

    # ---- routes ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                body = INDEX.read_bytes()
            except OSError:
                return self._error(500, "The page is missing on the server.", "no_index")
            return self._send(200, body, "text/html; charset=utf-8", {"Cache-Control": "no-cache"})
        if path == "/api/health":
            return self._json(200, {"ok": True, "version": config.VERSION})
        self._error(404, "Nothing here.", "not_found")

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/audit":
            return self._error(404, "Nothing here.", "not_found")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "The request had no length.", "no_length")
        if length < 0 or length > config.MAX_REQUEST_BYTES:
            self.close_connection = True
            return self._error(413, "The request is too large.", "too_large")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            url = data.get("url") if isinstance(data, dict) else None
        except (UnicodeDecodeError, ValueError):
            return self._error(400, "The request couldn't be read.", "bad_json")
        if not isinstance(url, str) or not url.strip():
            return self._error(400, "Please enter a web address.", "bad_url")
        if not audit_slots.acquire(blocking=False):
            return self._error(503, "Seora is busy checking other pages. Try again in a few seconds.", "busy")
        try:
            report = audit(url)
        except AuditError as e:
            return self._error(422, e.message, e.code)
        except Exception as e:
            log_failure("audit", e)
            return self._error(500, "Something went wrong on our side. Please try again.", "internal")
        finally:
            audit_slots.release()
        self._json(200, report)

    def do_PUT(self):
        self._error(405, "Not allowed.", "method")

    do_DELETE = do_PATCH = do_OPTIONS = do_PUT


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Never print a stack trace with request details; log the type and frames only
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return
        if exc is not None:
            log_failure("request", exc)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        httpd = Server((config.HOST, config.PORT), Handler)
    except OSError as e:
        raise SystemExit(f"Couldn't start on {config.HOST}:{config.PORT} ({e.strerror}). Is Seora already running? "
                         "Set SEORA_PORT to use another port.")
    shown = "localhost" if config.HOST in ("127.0.0.1", "::1") else config.HOST
    print(f"Seora {config.VERSION} is running at http://{shown}:{config.PORT}  (press Ctrl+C to stop)", flush=True)
    if not config.BLOCK_PRIVATE_ADDRESSES:
        print("WARNING: private-address blocking is OFF. Never expose this server to other people like this.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
