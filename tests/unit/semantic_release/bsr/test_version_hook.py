from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def minimal_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny repo PSR can run `version` on far enough to reach the guard seam."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
        'tag_format = "v{version}"\n',
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
