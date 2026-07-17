from __future__ import annotations

import pytest

from semantic_release.bsr import registry as reg
from semantic_release.bsr.registry import ProbeResult, probe_registry


@pytest.mark.parametrize(
    "status, expected",
    [(200, ProbeResult.EXISTS), (404, ProbeResult.FREE), (500, ProbeResult.UNKNOWN), (None, ProbeResult.UNKNOWN)],
)
def test_probe_maps_status(monkeypatch, status, expected):
    monkeypatch.setattr(reg, "_http_status", lambda _url, _timeout: status)
    assert probe_registry("pypi", "better-semantic-release", "1.2.3") is expected


def test_pypi_url_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        reg, "_http_status", lambda url, _timeout: captured.setdefault("url", url) or 404
    )
    probe_registry("pypi", "my-pkg", "1.2.3-beta.2")
    assert captured["url"] == "https://pypi.org/pypi/my-pkg/1.2.3-beta.2/json"


def test_npm_url_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        reg, "_http_status", lambda url, _timeout: captured.setdefault("url", url) or 404
    )
    probe_registry("npm", "@scope/pkg", "0.1.0")
    assert captured["url"] == "https://registry.npmjs.org/%40scope%2Fpkg/0.1.0"
