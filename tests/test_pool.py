"""Connection reuse (keep-alive) against a real local server. Run with:  python -m unittest discover -s tests

The local server is on 127.0.0.1, which the safety rules block, so these tests switch the block
off for themselves only (and check that it is on again for everything else).
"""

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import config
from seora import fetch


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    connections = set()
    close_after_one = False

    def log_message(self, *a):
        pass

    def _reply(self, status, body=b"", extra=()):
        Handler.connections.add(self.client_address)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        if Handler.close_after_one:
            self.close_connection = True

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.path == "/old":
            return self._reply(301, b"moved", [("Location", "/new")])
        if self.path == "/big":
            return self._reply(200, b"x" * 200_000)
        self._reply(200, b"<html>ok</html>", [("Content-Type", "text/html")])


class PoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass  # Seora closing a cut-off connection is expected

        cls.server = QuietServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.connections = set()
        Handler.close_after_one = False
        p1 = mock.patch.object(config, "BLOCK_PRIVATE_ADDRESSES", False)
        p2 = mock.patch.object(config, "ALLOWED_PORTS", frozenset({self.port}))
        p1.start(), p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_reuses_one_connection(self):
        pool = fetch.ConnectionPool()
        for path in ("/a", "/b", "/old", "/c"):
            r = fetch.fetch(self.url(path), method="HEAD" if path == "/b" else "GET", pool=pool)
            self.assertEqual(r.status, 200)
        pool.close()
        self.assertEqual(len(Handler.connections), 1)  # one TCP connection for 5 requests
        self.assertGreaterEqual(pool.reused, 4)

    def test_without_pool_each_fetch_is_new(self):
        fetch.fetch(self.url("/a"))
        fetch.fetch(self.url("/b"))
        self.assertEqual(len(Handler.connections), 2)

    def test_server_closing_connections_is_handled(self):
        Handler.close_after_one = True
        pool = fetch.ConnectionPool()
        for path in ("/a", "/b", "/c"):
            self.assertEqual(fetch.fetch(self.url(path), pool=pool).status, 200)
        pool.close()

    def test_cut_off_body_is_not_reused(self):
        pool = fetch.ConnectionPool()
        r = fetch.fetch(self.url("/big"), max_bytes=1000, pool=pool)
        self.assertTrue(r.truncated)
        self.assertEqual(fetch.fetch(self.url("/a"), pool=pool).status, 200)
        pool.close()
        self.assertEqual(pool.reused, 0)
        self.assertEqual(len(Handler.connections), 2)

    def test_closed_pool_takes_nothing(self):
        pool = fetch.ConnectionPool()
        pool.close()
        fetch.fetch(self.url("/a"), pool=pool)
        self.assertEqual(pool.take(("http", "127.0.0.1", self.port)), None)


class PoolSafety(unittest.TestCase):
    def test_block_is_back_on(self):
        self.assertTrue(config.BLOCK_PRIVATE_ADDRESSES)

    def test_pool_never_bypasses_the_address_check(self):
        """A connection only enters the pool after resolve() approved it, so a private host can't be reached
        even if something tried to reuse a key for it."""
        pool = fetch.ConnectionPool()
        with self.assertRaises(fetch.FetchError) as cm:
            fetch.fetch("http://127.0.0.1/", pool=pool)
        self.assertEqual(cm.exception.code, "blocked")
        self.assertEqual(pool._idle, {})


if __name__ == "__main__":
    unittest.main()
