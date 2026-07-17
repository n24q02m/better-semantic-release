from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path


def _build_minimal_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_pyproject: str = ""
) -> Path:
    """
    Build a tiny repo PSR can run `version` on far enough to reach the guard
    seam, optionally with extra `pyproject.toml` content (e.g. a `[tool.
    semantic_release.bsr]` table) baked in from the initial commit.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
        'tag_format = "v{version}"\n' + extra_pyproject,
        encoding="utf-8",
    )
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml"])
    repo.index.commit("feat: initial", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    # PSR resolves the hvcs client from the `origin` remote URL; without one,
    # `git remote get-url origin` fails before the command reaches the guard seam.
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def minimal_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny repo PSR can run `version` on far enough to reach the guard seam."""
    return _build_minimal_project(tmp_path, monkeypatch)


def test_guard_trip_exits_1(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**kwargs: object) -> None:
        raise BsrGuardError("BSR-BOOM-orphan")

    monkeypatch.setattr("semantic_release.cli.commands.version.run_guards", _boom)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 1
    assert "BSR-BOOM-orphan" in result.output


def test_guard_pass_proceeds(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(
        "semantic_release.cli.commands.version.run_guards",
        lambda **_kwargs: calls.__setitem__("n", calls["n"] + 1),
    )
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert calls["n"] == 1  # hook was reached exactly once


def _tag_already_computed_version(proj: Path) -> None:
    """
    Force PSR's `next_version` to collide with an existing tag (the orphan-tag /
    rewritten-history silent-freeze precondition): run `version --print` (no side
    effects) to learn what PSR would compute from `minimal_project`'s single
    `feat: initial` commit with no prior tags, then tag HEAD with exactly that
    version. The next real `version` invocation will then find `new_version` in
    `previously_released_versions`.
    """
    repo = Repo(str(proj))
    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    computed_version = printed.output.strip().splitlines()[-1]
    repo.create_tag(f"v{computed_version}")


def test_silent_freeze_escalates_to_exit_1(minimal_project: Path) -> None:
    """
    The real Case 1 silent-freeze: PSR recomputes an already-released version and,
    in non-strict mode, would normally `return` silently (exit 0). With
    `guard_orphan_tag` enabled (the default), this must instead fail loud.
    """
    _tag_already_computed_version(minimal_project)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 1
    assert "SILENT RELEASE FREEZE PREVENTED" in result.output
    assert "already been released" in result.output


def test_silent_freeze_opt_out_stays_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With `guard_orphan_tag = false`, the already-released case keeps PSR's
    original silent, exit-0 behavior (proving the opt-out).
    """
    proj = _build_minimal_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nguard_orphan_tag = false\n",
    )
    _tag_already_computed_version(proj)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "SILENT RELEASE FREEZE PREVENTED" not in result.output
    assert "already been released" in result.output
