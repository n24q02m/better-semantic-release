"""
End-to-end tests for the `semantic-release verify` command (W1.2).

Covers the verify matrix on real git fixture repositories, no network:
clean pass / fail-closed on an orphan tag / ``--offline`` SKIP path.
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


def _build_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, remote: bool = True
) -> Path:
    """A repo released once at `v0.1.0` with a feat commit on top."""
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
    if remote:
        repo.create_remote(
            "origin", "https://github.com/example-owner/example-repo.git"
        )
    _write(proj / "apps" / "web" / "y.py", "print('web v2')\n")
    repo.index.add(["apps/web/y.py"])
    repo.index.commit("feat: update web page", author=_AUTHOR, committer=_AUTHOR)

    monkeypatch.chdir(proj)
    return proj


def _build_orphan_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A repo whose HEAD is an unrelated root history: no reachable tags, and the feat commit on the unrelated root recomputes
    0.1.0 from the 0.0.0 base, colliding with the unreachable tag:
    the computed version stays at the released v0.1.0, but that tag is
    unreachable from HEAD -- verify must fail closed with ORPHAN_TAG.
    """
    proj = _build_repo(tmp_path, monkeypatch)
    repo = Repo(str(proj))
    repo.git.checkout("--orphan", "fresh-start")
    repo.index.commit("feat: unrelated restart", author=_AUTHOR, committer=_AUTHOR)
    repo.git.branch("-M", "main")
    return proj


def _run_verify(run_cli: RunCliFn, *args: str):
    return run_cli(["verify", *args], env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"})


def test_verify_clean_repo_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _run_verify(run_cli, "--offline")
    assert result.exit_code == 0
    out = str(result.stdout)
    assert "better-semantic-release doctor" in out
    assert "RESULT: OK" in out


def test_verify_fails_closed_on_orphan_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_orphan_repo(tmp_path, monkeypatch)
    result = _run_verify(run_cli, "--offline")
    assert result.exit_code == 1
    out = str(result.stdout)
    assert "ORPHAN_TAG" in out
    assert "RESULT: BLOCKED" in out


def test_verify_offline_skips_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _run_verify(run_cli, "--offline", "--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["blocked"] is False
    registry = next(c for c in doc["checks"] if c["code"] == "REGISTRY")
    assert registry["status"] == "skip"
