"""Unit tests for the doctor check catalog (`bsr.doctor`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semantic_release.bsr.doctor import (
    CheckResult,
    DoctorReport,
    check_branch_config,
    check_hvcs_token,
    check_missing_remote,
    check_prerelease_consistency,
    check_registry_state,
    check_tag_format,
    check_version_targets,
)
from semantic_release.bsr.registry import ProbeResult
from semantic_release.version.version import Version

from tests.unit.semantic_release.bsr.test_plan_cli import _build_repo

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_missing_remote_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    from git import Repo

    repo = Repo(str(proj))
    repo.delete_remote(repo.remotes.origin)
    result = check_missing_remote(repo)
    assert result.status == "fail"
    assert result.severity == "blocker"
    assert "git remote add" in result.fix


def test_missing_remote_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    from git import Repo

    result = check_missing_remote(Repo(str(proj)))
    assert result.status == "pass"


def test_tag_format_mismatch_detects_garbage_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    from git import Repo

    repo = Repo(str(proj))
    repo.create_tag("v1")  # unparseable under v{version}
    repo.create_tag("v1.0")
    translator = _translator()
    result = check_tag_format(repo, translator)
    assert result.status == "fail"
    assert result.severity == "warning"
    assert "2 tag(s)" in result.what


def test_tag_format_mismatch_passes_on_clean_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    from git import Repo

    result = check_tag_format(Repo(str(proj)), _translator())
    assert result.status == "pass"


def test_branch_config(tmp_path: Path) -> None:  # noqa: ARG001
    ok = check_branch_config(("(main|master)",), "main")
    assert ok.status == "pass"
    bad = check_branch_config(("(main|master)",), "feature/x")
    assert bad.status == "fail"
    assert bad.severity == "blocker"


def test_hvcs_token_severity_depends_on_release_intent() -> None:
    class _NoToken:  # hvcs clients without a token attribute
        pass

    warn_only = check_hvcs_token(_NoToken(), needs_release=False)
    assert warn_only.severity == "warning"
    assert warn_only.status == "fail"
    hard = check_hvcs_token(_NoToken(), needs_release=True)
    assert hard.severity == "blocker"

    class _WithToken:
        token = "secret"

    ok = check_hvcs_token(_WithToken(), needs_release=True)
    assert ok.status == "pass"


def test_prerelease_consistency() -> None:
    pre = Version(1, 2, 3, prerelease_token="rc", prerelease_revision=1)
    assert check_prerelease_consistency(True, pre).status == "pass"
    assert check_prerelease_consistency(False, pre).status == "fail"
    assert check_prerelease_consistency(False, Version(1, 2, 3)).status == "pass"


def test_registry_state_offline_skips() -> None:
    class _Cfg:
        registry = "pypi"

    result = check_registry_state(_Cfg(), "demo", "1.2.3", probe=None)
    assert result.status == "skip"
    assert "--check-registry" in result.fix


def test_registry_state_collision_and_unknown() -> None:
    class _Cfg:
        registry = "pypi"

    collision = check_registry_state(
        _Cfg(), "demo", "1.2.3", probe=lambda *_: ProbeResult.EXISTS
    )
    assert collision.code == "REGISTRY_COLLISION"
    assert collision.status == "fail"

    unknown = check_registry_state(
        _Cfg(), "demo", "1.2.3", probe=lambda *_: ProbeResult.UNKNOWN
    )
    assert unknown.code == "REGISTRY_PROBE_UNKNOWN"
    assert unknown.status == "fail"

    free = check_registry_state(
        _Cfg(), "demo", "1.2.3", probe=lambda *_: ProbeResult.FREE
    )
    assert free.status == "pass"


def test_registry_none_disables_checks() -> None:
    class _Cfg:
        registry = "none"

    result = check_registry_state(_Cfg(), "demo", "1.2.3", probe=None)
    assert result.status == "skip"


def test_bad_registry_config_fails() -> None:
    class _Cfg:
        registry = "npm-INVALID"

    result = check_registry_state(_Cfg(), "demo", "1.2.3", probe=None)
    assert result.code == "BAD_REGISTRY_CONFIG"
    assert result.status == "fail"
    assert result.severity == "blocker"


def test_report_blocked_and_warning_counts() -> None:
    report = DoctorReport(
        checks=(
            CheckResult(code="A", severity="blocker", status="pass", what="w"),
            CheckResult(code="B", severity="warning", status="fail", what="w"),
        )
    )
    assert report.blocked is False
    assert report.warning_count == 1

    blocked = DoctorReport(
        checks=(
            CheckResult(code="A", severity="blocker", status="fail", what="w"),
        )
    )
    assert blocked.blocked is True


def test_report_document_shape() -> None:
    report = DoctorReport(
        checks=(CheckResult(code="A", severity="blocker", status="pass", what="w"),)
    )
    doc = report.to_document()
    assert doc["schema_version"] == 1
    assert doc["blocked"] is False
    assert doc["checks"][0]["code"] == "A"


def test_version_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Decl:
        _path = "pyproject.toml"

        def parse(self):
            return {Version(0, 1, 0)}

    class _Runtime:
        version_declarations = (_Decl(),)

    ok = check_version_targets(_Runtime(), "0.1.0")
    assert ok.status == "pass"
    stale = check_version_targets(_Runtime(), "0.2.0")
    assert stale.status == "fail"
    assert "pyproject.toml" in stale.why

    skip = check_version_targets(_Runtime(), None)
    assert skip.status == "skip"


def _translator():
    from semantic_release.version.translator import VersionTranslator

    return VersionTranslator()




def test_check_version_consistency_agrees(tmp_path) -> None:
    from semantic_release.bsr.doctor import check_version_consistency
    from semantic_release.bsr.version_sources import (
        JsonVersionSource,
        TextVersionSource,
    )

    (tmp_path / "package.json").write_text('{"version": "1.0.0"}', encoding="utf-8")
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    result = check_version_consistency(
        [
            JsonVersionSource(tmp_path / "package.json", "version"),
            TextVersionSource(tmp_path / "VERSION", "{version}"),
        ]
    )
    assert result.code == "VERSION_CONSISTENCY"
    assert result.status == "pass"


def test_check_version_consistency_detects_drift(tmp_path) -> None:
    """
    Task 4 Step 4: pyproject.toml, package.json, VERSION carrying the same
    project version must agree; divergence names the drifting sources.
    """
    from semantic_release.bsr.doctor import check_version_consistency
    from semantic_release.bsr.version_sources import (
        JsonVersionSource,
        TextVersionSource,
        TomlVersionSource,
    )

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.0.0"\n', encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        '{"version": "1.1.0"}', encoding="utf-8"
    )
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")

    sources = [
        TomlVersionSource(tmp_path / "pyproject.toml", "project.version"),
        JsonVersionSource(tmp_path / "package.json", "version"),
        TextVersionSource(tmp_path / "VERSION", "{version}"),
    ]
    result = check_version_consistency(sources)
    assert result.code == "VERSION_CONSISTENCY"
    assert result.severity == "warning"
    assert result.status == "fail"
    assert "disagree" in result.what
    for fragment in ("toml:", "json:", "text:"):
        assert fragment in result.why


def test_check_version_consistency_all_agree(tmp_path) -> None:
    from semantic_release.bsr.doctor import check_version_consistency
    from semantic_release.bsr.version_sources import (
        JsonVersionSource,
        TextVersionSource,
    )

    (tmp_path / "package.json").write_text(
        '{"version": "2.0.0"}', encoding="utf-8"
    )
    (tmp_path / "VERSION").write_text("2.0.0\n", encoding="utf-8")
    result = check_version_consistency(
        [
            JsonVersionSource(tmp_path / "package.json", "version"),
            TextVersionSource(tmp_path / "VERSION", "{version}"),
        ]
    )
    assert result.status == "pass"
    assert "agree" in result.what


def test_check_version_consistency_empty_source(tmp_path) -> None:
    from semantic_release.bsr.doctor import check_version_consistency
    from semantic_release.bsr.version_sources import TextVersionSource

    (tmp_path / "VERSION").write_text("not a version\n", encoding="utf-8")
    result = check_version_consistency(
        [TextVersionSource(tmp_path / "VERSION", "{version}")]
    )
    assert result.status == "fail"
    assert "no version found" in result.why
