"""Unit tests for registry probe adapters (Task 5)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from semantic_release.bsr.registry import (
    PROBE_ADAPTERS,
    PROBE_KINDS,
    ProbeResult,
    ProbeTarget,
    probe_registry,
)

if TYPE_CHECKING:
    from pathlib import Path


def _patch_http(monkeypatch: pytest.MonkeyPatch, status: int | None) -> None:
    monkeypatch.setattr(
        "semantic_release.bsr.registry._http_status", lambda *_args, **_kwargs: status
    )


def test_probe_kinds_have_adapters() -> None:
    expected = {"none", "pypi", "npm", "crates", "oci", "github-release", "http"}
    assert sorted(PROBE_KINDS) == sorted(PROBE_ADAPTERS)
    assert sorted(PROBE_KINDS) == sorted(expected)


@pytest.mark.parametrize("kind", ["pypi", "npm", "crates"])
def test_registry_backed_kinds_classify(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    for status, expected in [
        (200, ProbeResult.EXISTS),
        (404, ProbeResult.FREE),
        (403, ProbeResult.UNKNOWN),
        (500, ProbeResult.UNKNOWN),
        (None, ProbeResult.UNKNOWN),
    ]:
        _patch_http(monkeypatch, status)
        assert (
            probe_registry(kind, "demo", "1.0.0") is expected
        ), f"{kind} status={status}"


def test_oci_requires_registry_url() -> None:
    with pytest.raises(ValueError, match="registry_url"):
        probe_registry("oci", "demo", "1.0.0")


def test_github_release_requires_repo() -> None:
    with pytest.raises(ValueError, match="repo"):
        probe_registry("github-release", "demo", "1.0.0", tag="v1.0.0")


def test_github_release_requires_tag() -> None:
    with pytest.raises(ValueError, match="tag"):
        probe_registry("github-release", "demo", "1.0.0", repo="acme/widgets")


def test_github_release_probes_the_tag_not_the_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def fake_status(url: str, timeout: float, extra_headers=None) -> int | None:
        seen["url"] = url
        return 200

    monkeypatch.setattr("semantic_release.bsr.registry._http_status", fake_status)
    result = probe_registry(
        "github-release", "demo", "1.2.3", tag="v1.2.3", repo="acme/widgets"
    )
    assert result is ProbeResult.EXISTS
    assert seen["url"].endswith("/releases/tags/v1.2.3")


def test_http_requires_url_template() -> None:
    with pytest.raises(ValueError, match="url_template"):
        probe_registry("http", "demo", "1.0.0")


def test_http_template_formats_name_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, str] = {}

    def fake_status(url: str, timeout: float, extra_headers=None) -> int | None:
        seen["url"] = url
        return 200

    monkeypatch.setattr(
        "semantic_release.bsr.registry._http_status",
        lambda url, timeout, extra_headers=None: fake_status(
            url, timeout, extra_headers
        ),
    )
    result = probe_registry(
        "http",
        "demo pkg",
        "1.0.0",
        url_template="https://example.com/{name}/{version}.json",
    )
    assert result is ProbeResult.EXISTS
    assert seen["url"] == "https://example.com/demo%20pkg/1.0.0.json"


def test_oci_probe_sends_accept_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_status(url: str, timeout: float, extra_headers=None) -> int | None:
        captured["url"] = url
        captured["headers"] = extra_headers
        return 404

    monkeypatch.setattr(
        "semantic_release.bsr.registry._http_status",
        lambda url, timeout, extra_headers=None: fake_status(
            url, timeout, extra_headers
        ),
    )
    result = probe_registry("oci", "demo/app", "v1.0.0", registry_url="ghcr.io")
    assert result is ProbeResult.FREE
    assert captured["url"] == "https://ghcr.io/v2/demo/app/manifests/v1.0.0"
    assert "oci" in str(captured["headers"])


def test_unknown_probe_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown registry probe kind"):
        probe_registry("gemfury", "demo", "1.0.0")


def test_none_probe_is_free() -> None:
    assert probe_registry("none", "demo", "1.0.0") is ProbeResult.FREE


def test_probe_target_defaults_are_empty() -> None:
    target = ProbeTarget(kind="pypi", name="demo", version="1.0.0")
    assert (target.repo, target.url_template, target.registry_url) == ("", "", "")
