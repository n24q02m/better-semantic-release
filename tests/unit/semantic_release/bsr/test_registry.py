from __future__ import annotations

import socket
import threading
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

import pytest

from semantic_release.bsr import registry as reg
from semantic_release.bsr.registry import ProbeResult, probe_registry

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://127.1",
        "http://0177.0.0.1",
        "http://localhost",
        "http://169.254.169.254",
        "http://[::1]",
    ],
)
def test_http_status_blocks_ssrf_targets(url: str) -> None:
    assert reg._http_status(url, timeout=1.0) is None


@pytest.mark.parametrize(
    "status, expected",
    [
        (200, ProbeResult.EXISTS),
        (404, ProbeResult.FREE),
        (500, ProbeResult.UNKNOWN),
        (None, ProbeResult.UNKNOWN),
    ],
)
def test_probe_maps_status(monkeypatch, status, expected):
    monkeypatch.setattr(reg, "_http_status", lambda _url, _timeout: status)
    assert probe_registry("pypi", "better-semantic-release", "1.2.3") is expected


def test_pypi_url_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        reg,
        "_http_status",
        lambda url, _timeout: captured.setdefault("url", url) or 404,
    )
    probe_registry("pypi", "my-pkg", "1.2.3-beta.2")
    assert captured["url"] == "https://pypi.org/pypi/my-pkg/1.2.3-beta.2/json"


def test_npm_url_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        reg,
        "_http_status",
        lambda url, _timeout: captured.setdefault("url", url) or 404,
    )
    probe_registry("npm", "@scope/pkg", "0.1.0")
    assert captured["url"] == "https://registry.npmjs.org/%40scope%2Fpkg/0.1.0"


def _make_fixed_status_handler(status_code: int) -> type[BaseHTTPRequestHandler]:
    class _FixedStatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(status_code)
            self.end_headers()

        def log_message(self, log_format: str, *args: object) -> None:  # noqa: ARG002
            pass  # silence request logging during tests

    return _FixedStatusHandler


@contextmanager
def _local_http_server(status_code: int) -> Iterator[str]:
    """Serve a real HTTP response with a fixed status code on localhost."""
    server = HTTPServer(("127.0.0.1", 0), _make_fixed_status_handler(status_code))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join()


def _closed_port_url() -> str:
    """Return a URL pointing at a port nothing is listening on."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # The socket is closed on context-exit above, so the port is free again
    # and connecting to it now fails with "connection refused".
    return f"http://127.0.0.1:{port}/"


@pytest.mark.parametrize("status_code", [200, 404])
def test_http_status_real_server_returns_status(status_code, monkeypatch):
    """
    `_http_status` against a real local HTTP server (no mocking) returns the
    server's actual status code, closing the "urllib path always mocked" gap.
    """
    # Bypass the SSRF protection temporarily for this test
    # since it specifically tests a real local server.
    import urllib.parse

    original_urlparse = urllib.parse.urlparse

    def _mock_urlparse(url_str):
        parsed = original_urlparse(url_str)
        if parsed.hostname == "127.0.0.1":
            return parsed._replace(netloc="example.com:0")
        return parsed

    monkeypatch.setattr(urllib.parse, "urlparse", _mock_urlparse)

    with _local_http_server(status_code) as url:
        assert reg._http_status(url, timeout=5.0) == status_code


def test_http_status_connection_failure_returns_none(monkeypatch):
    """
    `_http_status` returns `None` (fails closed) on a real connection
    failure, without touching the internet.
    """
    import urllib.parse

    original_urlparse = urllib.parse.urlparse

    def _mock_urlparse(url_str):
        parsed = original_urlparse(url_str)
        if parsed.hostname == "127.0.0.1":
            return parsed._replace(netloc="example.com:0")
        return parsed

    monkeypatch.setattr(urllib.parse, "urlparse", _mock_urlparse)

    assert reg._http_status(_closed_port_url(), timeout=2.0) is None


def test_http_status_ssrf_returns_none():
    """
    `_http_status` returns `None` when given a URL pointing to localhost
    or internal AWS metadata IP, preventing SSRF.
    """
    assert reg._http_status("http://127.0.0.1:80/", timeout=2.0) is None
    assert reg._http_status("http://localhost:80/", timeout=2.0) is None
    assert (
        reg._http_status("http://169.254.169.254/latest/meta-data/", timeout=2.0)
        is None
    )


def test_http_status_invalid_scheme_returns_none():
    """
    `_http_status` returns `None` when given a URL with an invalid scheme,
    such as `file://`, preventing SSRF/LFI.
    """
    assert reg._http_status("file:///etc/passwd", timeout=2.0) is None
