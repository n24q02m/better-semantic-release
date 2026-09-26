"""
End-to-end tests for the `semantic-release doctor` command (W1.2).

Covers the doctor matrix on a broken fixture repository (missing remote),
no network: report rendering in all formats plus exit-code semantics with
and without ``--strict``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.hvcs.github import Github

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.conftest import RunCliFn

_AUTHOR = Actor("demo", "demo@example.com")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_broken_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo with no remote at all: doctor must report a blocker, not crash."""
    proj = tmp_path / "proj"
    _write(
        proj / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n",
    )
    _write(proj / "apps" / "api" / "x.py", "print('api')\n")
    repo = Repo.init(proj, initial_branch="main")
    repo.index.add(["pyproject.toml", "apps/api/x.py"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")

    monkeypatch.chdir(proj)
    return proj


def _run_doctor(run_cli: RunCliFn, *args: str):
    return run_cli(["doctor", *args], env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"})


def test_doctor_renders_report_on_broken_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_broken_repo(tmp_path, monkeypatch)
    result = _run_doctor(run_cli)
    assert result.exit_code == 0  # not strict: report only
    out = str(result.stdout)
    assert "better-semantic-release doctor" in out
    assert "MISSING_REMOTE" in out
    assert "RESULT: BLOCKED" in out


def test_doctor_strict_exits_nonzero_on_broken_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_broken_repo(tmp_path, monkeypatch)
    result = _run_doctor(run_cli, "--strict")
    assert result.exit_code == 1


def test_doctor_renders_markdown_and_json_on_broken_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_broken_repo(tmp_path, monkeypatch)
    md = str(_run_doctor(run_cli, "--format", "markdown").stdout)
    assert "## Doctor report" in md
    assert "blocked: yes" in md
    assert "MISSING_REMOTE" in md

    json_result = _run_doctor(run_cli, "--format", "json")
    assert json_result.exit_code == 0
    doc = json.loads(str(json_result.stdout))
    assert doc["blocked"] is True
    codes = {c["code"] for c in doc["checks"]}
    assert "MISSING_REMOTE" in codes
