"""Fetch a job posting page from a URL the user pasted.

The URL is untrusted input and the request runs on the home server, so it must
not become a way to reach the home network. Only http and https are allowed,
every address the host resolves to must be public, the connection is pinned to
the address that was checked (so a second DNS answer cannot swap in a private
one), every redirect is checked again, and time and size are capped.
"""

import contextlib
import ipaddress
import logging
import socket
import threading
import time
from dataclasses import dataclass
from email.message import Message
from queue import Empty, Queue
from urllib.parse import urljoin, urlsplit

import urllib3
from urllib3.exceptions import (
    ConnectTimeoutError,
    HTTPError,
    NewConnectionError,
    ReadTimeoutError,
    SSLError,
)

logger = logging.getLogger(__name__)

MAX_BYTES = 3 * 1024 * 1024
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10
TOTAL_TIMEOUT = 20
MAX_REDIRECTS = 5
CHUNK_SIZE = 16 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; job-tracker/1.0; personal link preview)"
HTML_TYPES = {"text/html", "application/xhtml+xml"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_PORTS = {"http": 80, "https": 443}


class FetchError(Exception):
    """The page could not be fetched. The message is written for the user."""


@dataclass(frozen=True)
class FetchedPage:
    url: str
    content: bytes
    charset: str | None


def is_public_address(address):
    """True if connecting to this IP address cannot reach a private network."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6:
        # IPv6 forms that carry an IPv4 address are judged by that address.
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo and ip.teredo[1])
        if embedded and not is_public_address(embedded):
            return False
    return ip.is_global and not ip.is_multicast


def resolve_public_addresses(host, port, deadline):
    result = Queue(maxsize=1)

    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception as error:
            result.put(error)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        infos = result.get(timeout=max(0, deadline - time.monotonic()))
    except Empty as error:
        raise FetchError("The site took too long to answer.") from error
    if isinstance(infos, (socket.gaierror, UnicodeError)):
        error = infos
        raise FetchError(f"Could not find the site {host}.") from error
    if isinstance(infos, Exception):
        raise infos
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    blocked = [address for address in addresses if not is_public_address(address)]
    if not addresses or blocked:
        logger.warning("Refused to fetch %s: resolves to non-public %s", host, blocked)
        raise FetchError(
            "That link points to a private or local network address, which the tracker does "
            "not fetch."
        )
    return addresses


class DeadlineConnection:
    def __init__(self, *args, deadline, deadline_sockets, **kwargs):
        super().__init__(*args, **kwargs)
        self.deadline = deadline
        self.deadline_sockets = deadline_sockets

    def _new_conn(self):
        self.timeout = min(self.timeout, max(0.001, self.deadline - time.monotonic()))
        sock = super()._new_conn()
        self.deadline_sockets.append(sock.dup())
        if time.monotonic() >= self.deadline:
            sock.close()
            raise ConnectTimeoutError("The site took too long to answer.")
        return sock


class DeadlineHTTPConnection(DeadlineConnection, urllib3.connection.HTTPConnection):
    pass


class DeadlineHTTPSConnection(DeadlineConnection, urllib3.connection.HTTPSConnection):
    pass


def _host_header(host, port, scheme):
    if ":" in host:
        host = f"[{host}]"
    return host if port == DEFAULT_PORTS[scheme] else f"{host}:{port}"


def _open(url, deadline, deadline_sockets):
    """Send one GET request to the URL and return the unread response."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise FetchError("Only http and https links can be fetched.")
    if parts.username or parts.password:
        raise FetchError("Links with a user name or password are not fetched.")
    host = parts.hostname
    if not host:
        raise FetchError("The link has no host name.")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as error:
        raise FetchError("The link has an invalid port.") from error

    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    headers = {
        "Host": _host_header(host, port, scheme),
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en,da;q=0.8,*;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    last_error = None
    for address in resolve_public_addresses(host, port, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout = urllib3.Timeout(
            connect=min(CONNECT_TIMEOUT, remaining), read=min(READ_TIMEOUT, remaining)
        )
        if scheme == "https":
            # Connect to the checked address but verify the certificate against
            # the host name the user asked for.
            pool = urllib3.HTTPSConnectionPool(
                address,
                port,
                timeout=timeout,
                maxsize=1,
                retries=False,
                server_hostname=host,
                assert_hostname=host,
                cert_reqs="CERT_REQUIRED",
            )
        else:
            pool = urllib3.HTTPConnectionPool(
                address, port, timeout=timeout, maxsize=1, retries=False
            )
        pool.ConnectionCls = (
            DeadlineHTTPSConnection if scheme == "https" else DeadlineHTTPConnection
        )
        pool.conn_kw["deadline"] = deadline
        pool.conn_kw["deadline_sockets"] = deadline_sockets
        try:
            response = pool.urlopen(
                "GET",
                path,
                headers=headers,
                redirect=False,
                retries=False,
                preload_content=False,
                assert_same_host=False,
            )
            return response
        except (NewConnectionError, ConnectTimeoutError) as error:
            pool.close()
            last_error = error
            continue
        except SSLError as error:
            pool.close()
            raise FetchError(f"Could not open a secure connection to {host}.") from error
        except ReadTimeoutError as error:
            pool.close()
            raise FetchError(f"{host} took too long to answer.") from error
        except HTTPError as error:
            pool.close()
            raise FetchError(f"Could not connect to {host}.") from error
    if last_error is not None:
        logger.info("Connecting to %s failed: %s", host, last_error)
    raise FetchError(f"Could not connect to {host}.")


def http_error_message(status, host):
    if status in (404, 410):
        return (
            f"{host} says the posting does not exist (HTTP {status}). It may have been taken down."
        )
    if status in (401, 403, 429, 999):
        return (
            f"{host} refused to show the posting to the tracker (HTTP {status}). "
            "Some job boards block automatic requests."
        )
    return f"{host} answered with an error (HTTP {status})."


def _read_body(response, deadline):
    declared = response.headers.get("Content-Length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise FetchError(f"The page is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    chunks = []
    total = 0
    try:
        for chunk in response.stream(CHUNK_SIZE, decode_content=True):
            total += len(chunk)
            if total > MAX_BYTES:
                raise FetchError(f"The page is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            if time.monotonic() > deadline:
                raise FetchError("The site took too long to send the page.")
            chunks.append(chunk)
    except ReadTimeoutError as error:
        raise FetchError("The site took too long to send the page.") from error
    except HTTPError as error:
        raise FetchError("The connection broke while reading the page.") from error
    if time.monotonic() >= deadline:
        raise FetchError("The site took too long to send the page.")
    return b"".join(chunks)


def _fetch_page(url, deadline, deadline_sockets):
    for _ in range(MAX_REDIRECTS + 1):
        response = _open(url, deadline, deadline_sockets)
        try:
            host = urlsplit(url).hostname
            if response.status in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    raise FetchError(f"{host} redirected without saying where to.")
                url = urljoin(url, location.strip())
                continue
            if response.status >= 400:
                raise FetchError(http_error_message(response.status, host))
            header = Message()
            header["Content-Type"] = response.headers.get("Content-Type", "")
            mime = header.get_content_type()
            if response.headers.get("Content-Type") and mime not in HTML_TYPES:
                raise FetchError(f"The link is not a web page (it is {mime}).")
            content = _read_body(response, deadline)
            logger.info("Fetched %s (%d bytes)", url, len(content))
            return FetchedPage(url=url, content=content, charset=header.get_content_charset())
        finally:
            response.release_conn()
            response.close()
            if response._pool is not None:
                response._pool.close()
    raise FetchError("The link redirected too many times.")


def fetch_page(url):
    """Fetch an HTML page, following redirects, or raise FetchError."""
    deadline = time.monotonic() + TOTAL_TIMEOUT
    deadline_sockets = []

    def expire():
        for sock in deadline_sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
    timer.daemon = True
    timer.start()
    try:
        return _fetch_page(url, deadline, deadline_sockets)
    finally:
        timer.cancel()
        for sock in deadline_sockets:
            sock.close()
