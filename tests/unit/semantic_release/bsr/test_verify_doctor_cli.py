"""
End-to-end tests for `semantic-release verify` and `doctor`.

Task 3 Step 4: real broken fixtures — a repo with unparseable tags, a missing
remote, a non-release branch — must produce structured findings, and `verify`
must fail closed (exit 1) when a policy blocker trips.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.cli.commands.main import main

from tests.conftest import get_cli_runner

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("demo", "demo@example.com")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str = "main",
    remote: bool = True,
    garbage_tags: bool = False,
    token: str | None = "test-token",
) -> Path:
    if token is None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GH_TOKEN", token)
    proj = tmp_path / "proj"
    _write(
        proj / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n",
    )
    _write(proj / "apps" / "api" / "x.py", "print('api')\n")
    repo = Repo.init(proj, initial_branch=branch)
    repo.index.add(["pyproject.toml", "apps/api/x.py"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")
    if remote:
        repo.create_remote(
            "origin", "https://github.com/example-owner/example-repo.git"
        )
    if garbage_tags:
        repo.create_tag("v1")
        repo.create_tag("v1.0")
    monkeypatch.chdir(proj)
    return proj


def _invoke(*args: str) -> object:
    # Env is passed through CliRunner's own env mechanism so the HVCS token
    # resolution sees it regardless of how the runner isolates os.environ.
    return get_cli_runner().invoke(
        main, ["--noop", *args], env={"GH_TOKEN": "test-token"}
    )


def test_verify_offline_clean_repo_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("verify", "--offline")
    assert result.exit_code == 0
    out = str(result.stdout)
    assert "[pass] blocker" in out
    assert "RESULT: OK" in out


def test_verify_json_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("verify", "--offline", "--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["blocked"] is False
    codes = {c["code"] for c in doc["checks"]}
    assert {"MISSING_REMOTE", "BRANCH_CONFIG", "HVCS_TOKEN", "REGISTRY"} <= codes


def test_verify_fails_on_missing_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, remote=False)
    result = _invoke("verify", "--offline")
    assert result.exit_code == 1
    assert "MISSING_REMOTE" in str(result.stdout)
    assert "BLOCKED" in str(result.stdout)


def test_verify_fails_on_non_release_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, branch="feature/side")
    result = _invoke("verify", "--offline")
    assert result.exit_code == 1
    assert "BRANCH_CONFIG" in str(result.stdout)


def test_doctor_reports_warnings_and_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, garbage_tags=True)
    result = _invoke("doctor")
    assert result.exit_code == 0
    out = str(result.stdout)
    assert "TAG_FORMAT_MISMATCH" in out
    assert "REGISTRY" in out
    assert "RESULT:" in out


def test_doctor_json_has_all_catalog_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("doctor", "--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    codes = {c["code"] for c in doc["checks"]}
    assert {
        "MISSING_REMOTE",
        "TAG_FORMAT_MISMATCH",
        "BRANCH_CONFIG",
        "PRERELEASE_MISMATCH",
        "HVCS_TOKEN",
        "VERSION_TARGETS",
        "VERSION_CONSISTENCY",
        "TRUSTED_PUBLISHING",
        "REGISTRY",
    } <= codes
    for check in doc["checks"]:
        assert set(check) == {"code", "severity", "status", "what", "why", "fix"}


def test_doctor_strict_exits_nonzero_when_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, remote=False)
    result = _invoke("doctor", "--strict")
    assert result.exit_code == 1


def test_plan_verify_doctor_never_mutate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    repo = Repo(str(proj))
    head_before = str(repo.head.commit)
    for cmd in ("plan", "verify --offline", "doctor"):
        _invoke(*cmd.split())
    repo = Repo(str(proj))
    assert [str(t) for t in repo.tags] == ["v0.1.0"]
    assert str(repo.head.commit) == head_before
