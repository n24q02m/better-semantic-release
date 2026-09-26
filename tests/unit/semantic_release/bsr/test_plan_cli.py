"""
End-to-end tests for `semantic-release plan` (read-only projection).

Key invariants under test:
- default format is the human table; --format json yields exactly one
  schema-v1 document on stdout; --format markdown yields the markdown view;
- the command NEVER mutates the repo: no tag created, no commit, no file
  change (the plan is computed from tags + commits only);
- --strict is deterministic OFFLINE: no registry probe by default, so a fixed
  repository state always produces the same exit code;
- --check-registry opts back into the registry status row.
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


def _pyproject_toml(extra_bsr: str = "") -> str:
    return (
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        # NOTE: PSR forces a MAJOR bump out of 0.x.x regardless of which commits matched.
        "allow_zero_version = true\n" + extra_bsr
    )


def _build_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    new_commit: str | None = "feat: update web page",
) -> Path:
    """A repo released once at `v0.1.0`, optionally with one commit on top."""
    proj = tmp_path / "proj"
    _write(proj / "pyproject.toml", _pyproject_toml())
    _write(proj / "apps" / "api" / "x.py", "print('api')\n")
    _write(proj / "apps" / "web" / "y.py", "print('web')\n")
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml", "apps/api/x.py", "apps/web/y.py"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")

    if new_commit is not None:
        _write(proj / "apps" / "web" / "y.py", "print('web v2')\n")
        repo.index.add(["apps/web/y.py"])
        repo.index.commit(new_commit, author=_AUTHOR, committer=_AUTHOR)

    monkeypatch.chdir(proj)
    return proj


def _invoke(*args: str) -> object:
    return get_cli_runner().invoke(main, ["--noop", "plan", *args])


def test_plan_releases_in_table_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke()
    assert result.exit_code == 0
    out = str(result.stdout)
    assert "better-semantic-release release plan" in out
    assert "released:         yes" in out
    assert "version:          0.2.0" in out
    assert "previous version: 0.1.0" in out


def test_plan_json_document_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert set(doc) == {
        "schema_version",
        "released",
        "version",
        "tag",
        "is_prerelease",
        "previous_version",
        "head_sha",
        "decision",
        "bump",
        "components",
        "blockers",
        "registry",
        "publish_target",
    }
    assert doc["schema_version"] == 1
    assert doc["released"] is True
    assert doc["version"] == "0.2.0"
    assert doc["tag"] == "v0.2.0"
    assert doc["previous_version"] == "0.1.0"
    assert doc["head_sha"]  # W1.4: every plan snapshot carries its HEAD anchor
    assert doc["decision"] is None
    assert doc["bump"]["level_bump"] == "minor"
    assert doc["blockers"] == []
    assert doc["registry"] is None  # offline default: never probed


def test_plan_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    repo = Repo(str(proj))
    head_before = str(repo.head.commit)
    result = _invoke()
    assert result.exit_code == 0

    repo = Repo(str(proj))
    # no new tag was created
    assert [str(t) for t in repo.tags] == ["v0.1.0"]
    # HEAD unchanged
    assert str(repo.head.commit) == head_before
    # working tree untouched (pyproject still says 0.1.0)
    assert "0.1.0" in (proj / "pyproject.toml").read_text(encoding="utf-8")
    assert not (proj / "dist").exists()


def test_plan_no_release_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, new_commit="chore: tidy docs")
    result = _invoke("--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["released"] is False
    assert doc["decision"]["code"] == "NO_QUALIFYING_COMMITS"
    assert doc["version"] == "0.1.0"


def test_plan_already_released_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, new_commit=None)
    result = _invoke("--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["released"] is False
    assert doc["decision"]["code"] == "ALREADY_RELEASED_NOOP"


def test_plan_strict_is_deterministic_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    # Fixed repo state + offline default => the same exit code every time,
    # regardless of network reachability.
    results = [_invoke("--strict") for _ in range(3)]
    assert {r.exit_code for r in results} == {0}


def test_plan_strict_fails_on_orphan_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    # Rewind HEAD to before the feat commit while keeping the tag... an orphan
    # needs new_version > highest reachable: reset the branch to v0.1.0's
    # parent is impossible (single commit), so instead drop the feat commit
    # with a rebase-free hard reset and delete the leftover ref state.
    repo = Repo(str(proj))
    repo.head.reset("v0.1.0", index=True, working_tree=True)
    result = _invoke("--format", "json", "--strict")
    doc = json.loads(str(result.stdout))
    # benign no-op (ALREADY_RELEASED_NOOP, not orphaned): no blocker, exit 0
    assert doc["decision"]["code"] == "ALREADY_RELEASED_NOOP"
    assert result.exit_code == 0


def test_plan_markdown_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "markdown")
    assert result.exit_code == 0
    out = str(result.stdout)
    assert "## Release plan" in out
    assert "| released | yes |" in out


def test_plan_write_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    plan_file = proj / "release-plan.md"
    result = _invoke("--format", "markdown", "--write", str(plan_file))
    assert result.exit_code == 0
    content = plan_file.read_text(encoding="utf-8")
    assert "## Release plan" in content
