"""
End-to-end tests for the `semantic-release plan` command (W1.2).

Covers the plan matrix on real git fixture repositories, no network:
release / no-release / blocked (orphan guard) / ``--write`` artifact /
format parity (json vs table) / ``--strict`` exit codes.
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str = "proj",
    new_commit: str | None = "feat: update web page",
) -> Path:
    """A repo released once at `v0.1.0`, optionally with one commit on top."""
    proj = tmp_path / name
    _write(
        proj / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n",
    )
    _write(proj / "apps" / "api" / "x.py", "print('api')\n")
    _write(proj / "apps" / "web" / "y.py", "print('web')\n")
    repo = Repo.init(proj, initial_branch="main")
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


def _build_orphan_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    A repo whose HEAD is an unrelated root history: no reachable tags, and the feat commit on the unrelated root recomputes
    0.1.0 from the 0.0.0 base, colliding with the unreachable tag:
    the computed version stays at the released v0.1.0, but that tag is
    unreachable from HEAD -- the orphan guard trips (blocked plan).
    """
    proj = _build_repo(tmp_path, monkeypatch)
    repo = Repo(str(proj))
    repo.git.checkout("--orphan", "fresh-start")
    repo.index.commit("feat: unrelated restart", author=_AUTHOR, committer=_AUTHOR)
    repo.git.branch("-M", "main")
    return proj


def _run_plan(run_cli: RunCliFn, *args: str):
    return run_cli(["plan", *args], env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"})


def test_plan_release_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _run_plan(run_cli, "--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["released"] is True
    assert doc["version"] == "0.2.0"
    assert doc["tag"] == "v0.2.0"
    assert doc["previous_version"] == "0.1.0"
    assert doc["blockers"] == []


def test_plan_no_release_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_repo(tmp_path, monkeypatch, new_commit="chore: tidy docs")
    result = _run_plan(run_cli, "--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["released"] is False
    assert doc["decision"]["code"] == "NO_QUALIFYING_COMMITS"
    assert doc["version"] == "0.1.0"


def test_plan_blocked_orphan_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_orphan_repo(tmp_path, monkeypatch)
    # Without --strict the plan still succeeds but records the blocker.
    result = _run_plan(run_cli, "--format", "json")
    assert result.exit_code == 0
    doc = json.loads(str(result.stdout))
    assert doc["released"] is False
    assert doc["decision"]["code"] == "ORPHAN"
    assert doc["blockers"][0]["code"] == "ORPHAN_TAG"


def test_plan_write_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    plan_file = proj / "release-plan.md"
    result = _run_plan(run_cli, "--format", "markdown", "--write", str(plan_file))
    assert result.exit_code == 0
    # Byte-exact artifact golden (W1.3): the artifact is the rendered markdown
    # plus the trailing newline, and stdout carries the same bytes. (The
    # artifact is written in text mode, so newlines are normalised for the
    # platform before comparing.)
    content = plan_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    stdout = str(result.stdout).replace("\r\n", "\n")
    assert content == _GOLDEN_MARKDOWN_ARTIFACT
    assert stdout == _GOLDEN_MARKDOWN_ARTIFACT


def test_plan_write_artifact_table_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    plan_file = proj / "release-plan.txt"
    result = _run_plan(run_cli, "--format", "table", "--write", str(plan_file))
    assert result.exit_code == 0
    content = plan_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    stdout = str(result.stdout).replace("\r\n", "\n")
    assert content == _GOLDEN_TABLE_ARTIFACT
    assert stdout == _GOLDEN_TABLE_ARTIFACT


def test_plan_json_stdout_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    """The `--format json` stdout is byte-exact against the schema-v1 serialization."""
    _build_repo(tmp_path, monkeypatch)
    stdout = str(_run_plan(run_cli, "--format", "json").stdout)
    assert stdout == _GOLDEN_JSON_STDOUT
    assert isinstance(json.loads(stdout), dict)


def test_plan_format_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_repo(tmp_path, monkeypatch)
    doc = json.loads(str(_run_plan(run_cli, "--format", "json").stdout))
    table = str(_run_plan(run_cli, "--format", "table").stdout)
    assert "better-semantic-release release plan" in table
    assert f"released:         {'yes' if doc['released'] else 'no'}" in table
    assert f"version:          {doc['version']}" in table
    assert f"tag:              {doc['tag']}" in table


def test_plan_strict_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    # Release pending => exit 0 under --strict.
    _build_repo(tmp_path, monkeypatch, name="release-repo")
    assert _run_plan(run_cli, "--strict").exit_code == 0

    # Benign no-release (nothing new since the tag) => exit 0 under --strict.
    _build_repo(tmp_path, monkeypatch, name="noop-repo", new_commit=None)
    assert _run_plan(run_cli, "--strict").exit_code == 0

    # Blocked (orphan guard) => exit 1 under --strict.
    _build_orphan_repo(tmp_path, monkeypatch)
    assert _run_plan(run_cli, "--strict").exit_code == 1


# ---------------------------------------------------------------------------
# W1.3 byte-exact goldens for the default release fixture (v0.1.0 -> 0.2.0).
# Each string is the rendered plan plus the trailing newline; stdout and the
# `--write` artifact must both carry exactly these bytes.


_GOLDEN_TABLE_ARTIFACT_LINES = [
    "better-semantic-release release plan",
    "  released:         yes",
    "  version:          0.2.0",
    "  tag:              v0.2.0",
    "  previous version: 0.1.0",
    "  reason:           -",
    "  level bump:       minor",
    "  commits:          1",
    "",
]
_GOLDEN_TABLE_ARTIFACT = "\n".join(_GOLDEN_TABLE_ARTIFACT_LINES)

_GOLDEN_MARKDOWN_ARTIFACT_LINES = [
    "## Release plan",
    "",
    "| field | value |",
    "| --- | --- |",
    "| released | yes |",
    "| version | `0.2.0` |",
    "| tag | `v0.2.0` |",
    "| previous version | `0.1.0` |",
    "| reason | - |",
    "",
]
_GOLDEN_MARKDOWN_ARTIFACT = "\n".join(_GOLDEN_MARKDOWN_ARTIFACT_LINES)

_GOLDEN_JSON_STDOUT = """\
{
  "schema_version": 1,
  "released": true,
  "version": "0.2.0",
  "tag": "v0.2.0",
  "is_prerelease": false,
  "previous_version": "0.1.0",
  "decision": null,
  "bump": {
    "level_bump": "minor",
    "commit_count": 1,
    "type_counts": {
      "features": 1
    }
  },
  "components": [],
  "blockers": [],
  "registry": null,
  "publish_target": null
}
"""
