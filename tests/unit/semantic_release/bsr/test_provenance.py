"""Unit tests for publish readiness / provenance posture (Task 8)."""

from __future__ import annotations

import pytest

from semantic_release.bsr.provenance import (
    assess_publish_readiness,
    supports_provenance,
)


def test_pypi_and_npm_support_provenance() -> None:
    assert supports_provenance("pypi") is True
    assert supports_provenance("npm") is True
    assert supports_provenance("github-release") is False
    assert supports_provenance("crates") is False
    assert supports_provenance("oci") is False
    assert supports_provenance("shell") is None
    assert supports_provenance("none") is None


def test_trusted_publishing_environment_is_ready() -> None:
    env = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.githubusercontent.com",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "tok",
    }
    result = assess_publish_readiness("pypi", environment=env)
    assert result.ready is True
    assert result.supports_provenance is True
    assert "OIDC" in result.detail


def test_legacy_password_is_reported_not_ready() -> None:
    env = {"PYPI_PASSWORD": "hunter2"}
    result = assess_publish_readiness("pypi", environment=env)
    assert result.ready is False
    assert "legacy" in result.detail
    assert "trusted" in result.detail


def test_missing_credentials_entirely_not_ready() -> None:
    result = assess_publish_readiness("npm", environment={})
    assert result.ready is False
    assert "no OIDC" in result.detail


def test_crates_ready_with_token() -> None:
    result = assess_publish_readiness(
        "crates", environment={"CARGO_REGISTRY_TOKEN": "t"}
    )
    assert result.ready is True


def test_crates_without_token_not_ready() -> None:
    result = assess_publish_readiness("crates", environment={})
    assert result.ready is False
    assert "CARGO_REGISTRY_TOKEN" in result.detail


def test_none_and_github_release_trivially_ready() -> None:
    for kind in ("none", "github-release"):
        result = assess_publish_readiness(kind, environment={})
        assert result.ready is True


def test_unknown_kind_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown publish kind"):
        # unknown kinds have no posture defined -> must not silently pass
        assess_publish_readiness("gemfury", environment={})
