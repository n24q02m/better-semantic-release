"""
A forced prerelease (`--prerelease`, the action's `force: prerelease`) never
releases below the version the commits ask for.

The flag exists so a beta can be published when no commit since the last
release qualifies, for example to refresh release-bound artifacts. Upstream
treats it as an override instead: the result is always the next prerelease
revision, so a `feat:` merged after 1.14.1 shipped as 1.14.2-beta.1 although
the stable release of the same commits is 1.15.0. The forced version is now a
floor under that stable target, so the beta line predicts the stable release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from semantic_release.cli.commands.main import main

from tests.conftest import get_cli_runner

if TYPE_CHECKING:
    from pathlib import Path

_AUTHOR = Actor("t", "t@t")
_FORCED_BETA = ("--as-prerelease", "--prerelease", "--prerelease-token", "beta")


def _commit(repo: Repo, proj: Path, message: str) -> None:
    note = proj / "notes.txt"
    note.write_text(note.read_text(encoding="utf-8") + message + "\n", encoding="utf-8")
    repo.index.add(["notes.txt"])
    repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _build_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: list[str],
) -> None:
    """
    A repo released at `v1.14.1`, then `history` replayed on top.

    An entry starting with `tag:` tags HEAD; any other entry is a commit
    message.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.14.1"\n\n[tool.semantic_release]\n',
        encoding="utf-8",
    )
    (proj / "notes.txt").write_text("", encoding="utf-8")
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml", "notes.txt"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v1.14.1")
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    for entry in history:
        if entry.startswith("tag:"):
            repo.create_tag(entry.removeprefix("tag:"))
        else:
            _commit(repo, proj, entry)
    monkeypatch.chdir(proj)


def _print_version(*args: str) -> str:
    result = get_cli_runner().invoke(
        main,
        ["--noop", "version", "--no-commit", "--no-tag", "--no-push", "--print", *args],
    )
    assert result.exit_code == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize(
    "history, expected",
    [
        # The commits ask for a minor release: the forced beta must not undercut it.
        (["feat: add a guard"], "1.15.0-beta.1"),
        (["fix: a bug", "feat: add a guard", "fix: another bug"], "1.15.0-beta.1"),
        (["feat!: drop the old flag"], "2.0.0-beta.1"),
        # A patch-level history: the floor and the commits agree.
        (["fix: a bug"], "1.14.2-beta.1"),
        # Nothing qualifies: the forced maintenance beta still releases.
        (["chore: refresh"], "1.14.2-beta.1"),
        # An earlier forced beta already undercut a feature: the next one
        # recovers the version the commits ask for.
        (["feat: add a guard", "tag:v1.14.2-beta.1", "fix: a bug"], "1.15.0-beta.1"),
        # HEAD already carries the latest beta: count its revision up.
        (["feat: add a guard", "tag:v1.15.0-beta.1"], "1.15.0-beta.2"),
        (
            ["feat: add a guard", "tag:v1.15.0-beta.1", "fix: a bug"],
            "1.15.0-beta.2",
        ),
    ],
)
def test_forced_prerelease_is_a_floor_under_the_commit_bump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: list[str],
    expected: str,
) -> None:
    _build_repo(tmp_path, monkeypatch, history)

    assert _print_version(*_FORCED_BETA) == expected + "\n"


def test_stable_release_after_an_undercut_beta_follows_the_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stable release already counted every commit since 1.14.1."""
    _build_repo(tmp_path, monkeypatch, ["feat: add a guard", "tag:v1.14.2-beta.1"])

    assert _print_version() == "1.15.0\n"


def test_unforced_prerelease_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch, ["feat: add a guard"])

    assert _print_version("--as-prerelease", "--prerelease-token", "beta") == (
        "1.15.0-beta.1\n"
    )


@pytest.mark.parametrize(
    "flag, expected",
    [("--patch", "1.14.2"), ("--minor", "1.15.0"), ("--major", "2.0.0")],
)
def test_other_forced_levels_still_override_the_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flag: str, expected: str
) -> None:
    _build_repo(tmp_path, monkeypatch, ["feat: add a guard"])

    assert _print_version(flag) == expected + "\n"
