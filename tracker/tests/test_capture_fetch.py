"""The fetcher against a local HTTP server, so redirects and limits are real."""

import contextlib
import gzip
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tracker.capture import fetch
from tracker.capture.fetch import FetchError, fetch_page, is_public_address


@pytest.mark.parametrize(
    "address,public",
    [
        ("93.184.216.34", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.8", False),
        ("172.16.5.4", False),
        ("192.168.1.10", False),
        ("169.254.169.254", False),
        ("100.101.102.103", False),  # Tailscale and carrier-grade NAT
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd7a:115c:a1e0::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:192.168.1.10", False),
        ("2002:c0a8:010a::1", False),  # 6to4 wrapping 192.168.1.10
        ("not an address", False),
    ],
)
def test_is_public_address(address, public):
    assert is_public_address(address) is public


def fake_dns(monkeypatch, *addresses):
    def getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", getaddrinfo)


def test_host_resolving_to_a_private_address_is_refused(monkeypatch):
    fake_dns(monkeypatch, "192.168.1.10")
    with pytest.raises(FetchError, match="private or local network"):
        fetch_page("https://jobs.example/1")


def test_host_with_any_private_address_is_refused(monkeypatch):
    fake_dns(monkeypatch, "93.184.216.34", "10.0.0.1")
    with pytest.raises(FetchError, match="private or local network"):
        fetch_page("https://jobs.example/1")


@pytest.mark.parametrize(
    "url,message",
    [
        ("ftp://jobs.example/1", "Only http and https"),
        ("file:///etc/passwd", "Only http and https"),
        ("https://user:secret@jobs.example/1", "user name or password"),
        ("https://jobs.example:99999/1", "invalid port"),
        ("http://127.0.0.1/", "private or local network"),
        ("http://[::1]/", "private or local network"),
        ("http://2130706433/", "private or local network"),
    ],
)
def test_unsafe_urls_are_refused_before_connecting(url, message):
    with pytest.raises(FetchError, match=message):
        fetch_page(url)


class Handler(BaseHTTPRequestHandler):
    routes = {}

    def do_GET(self):
        route = self.routes.get(self.path)
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        status, headers, body = route(self)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server(monkeypatch):
    """A local server the fetcher may reach; every other private address stays blocked."""
    Handler.routes = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    allowed = fetch.is_public_address
    monkeypatch.setattr(
        fetch, "is_public_address", lambda address: address == "127.0.0.1" or allowed(address)
    )
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, Handler.routes
    httpd.shutdown()
    httpd.server_close()


HTML = {"Content-Type": "text/html; charset=utf-8"}


def test_fetches_html_and_follows_redirects(server):
    base, routes = server
    routes["/short"] = lambda h: (302, {"Location": "/jobs/1"}, b"")
    routes["/jobs/1"] = lambda h: (200, HTML, "<title>Ølstykke</title>".encode())

    page = fetch_page(f"{base}/short")

    assert page.url == f"{base}/jobs/1"
    assert page.content == "<title>Ølstykke</title>".encode()
    assert page.charset == "utf-8"


def test_sends_the_requested_host_name(server):
    base, routes = server
    seen = {}

    def record(handler):
        seen.update(handler.headers)
        return 200, HTML, b"ok"

    routes["/"] = record
    fetch_page(f"{base}/")

    assert seen["Host"] == base.removeprefix("http://")
    assert "job-tracker" in seen["User-Agent"]


def test_decompresses_gzip(server):
    base, routes = server
    body = gzip.compress(b"<html>compressed</html>")
    routes["/"] = lambda h: (200, {**HTML, "Content-Encoding": "gzip"}, body)

    assert fetch_page(f"{base}/").content == b"<html>compressed</html>"


def test_redirect_to_a_private_address_is_refused(server):
    base, routes = server
    routes["/jobs/1"] = lambda h: (301, {"Location": "http://10.0.0.1/admin"}, b"")

    with pytest.raises(FetchError, match="private or local network"):
        fetch_page(f"{base}/jobs/1")


def test_redirect_loop_stops(server):
    base, routes = server
    routes["/loop"] = lambda h: (302, {"Location": "/loop"}, b"")

    with pytest.raises(FetchError, match="redirected too many times"):
        fetch_page(f"{base}/loop")


@pytest.mark.parametrize(
    "status,message",
    [
        (404, "does not exist .HTTP 404.. It may have been taken down"),
        (403, "refused to show the posting"),
        (999, "refused to show the posting"),
        (500, "answered with an error .HTTP 500."),
    ],
)
def test_error_statuses_explain_themselves(server, status, message):
    base, routes = server
    routes["/"] = lambda h: (status, HTML, b"nope")

    with pytest.raises(FetchError, match=message):
        fetch_page(f"{base}/")


def test_non_html_is_refused(server):
    base, routes = server
    routes["/cv.pdf"] = lambda h: (200, {"Content-Type": "application/pdf"}, b"%PDF-1.7")

    with pytest.raises(FetchError, match="not a web page .it is application/pdf."):
        fetch_page(f"{base}/cv.pdf")


def test_declared_oversized_page_is_refused(server, monkeypatch):
    base, routes = server
    monkeypatch.setattr(fetch, "MAX_BYTES", 1024)
    routes["/"] = lambda h: (200, {**HTML, "Content-Length": "4096"}, b"x" * 4096)

    with pytest.raises(FetchError, match="larger than"):
        fetch_page(f"{base}/")


def test_streamed_oversized_page_is_cut_off(server, monkeypatch):
    base, routes = server
    monkeypatch.setattr(fetch, "MAX_BYTES", 1024)
    monkeypatch.setattr(fetch, "CHUNK_SIZE", 256)
    # No Content-Length: the server just keeps sending until the connection closes.
    routes["/"] = lambda h: (200, {**HTML, "Connection": "close"}, b"x" * 100_000)

    with pytest.raises(FetchError, match="larger than"):
        fetch_page(f"{base}/")


def test_slow_server_times_out(server, monkeypatch):
    base, routes = server
    monkeypatch.setattr(fetch, "READ_TIMEOUT", 0.2)

    def slow(handler):
        time.sleep(1)
        return 200, HTML, b"late"

    routes["/"] = slow
    with pytest.raises(FetchError, match="took too long"):
        fetch_page(f"{base}/")


def test_unreachable_port_reports_a_connection_failure(server):
    base, _ = server
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    with pytest.raises(FetchError, match="Could not connect"):
        fetch_page(f"http://127.0.0.1:{free_port}/")


def test_unknown_host_is_reported(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fail)
    with pytest.raises(FetchError, match="Could not find the site jobs.example"):
        fetch_page("https://jobs.example/1")


@pytest.mark.parametrize("stage", ["headers", "body"])
def test_shared_deadline_interrupts_trickling_server(server, monkeypatch, stage):
    base, routes = server
    monkeypatch.setattr(fetch, "TOTAL_TIMEOUT", 0.2)

    def trickle(handler):
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            if stage == "body":
                handler.send_response(200)
                handler.send_header("Content-Type", "text/html")
                handler.end_headers()
            else:
                handler.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            for _ in range(30):
                handler.wfile.write(b"x")
                handler.wfile.flush()
                time.sleep(0.05)
        return 200, HTML, b""

    routes["/"] = trickle
    started = time.monotonic()
    with pytest.raises(FetchError):
        fetch_page(f"{base}/")
    assert time.monotonic() - started < 0.8


def test_shared_deadline_bounds_dns(monkeypatch):
    monkeypatch.setattr(fetch, "TOTAL_TIMEOUT", 0.2)

    def slow_dns(*args, **kwargs):
        time.sleep(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", slow_dns)
    started = time.monotonic()
    with pytest.raises(FetchError, match="took too long"):
        fetch_page("http://jobs.example/")
    assert time.monotonic() - started < 0.8
