"""
Unit tests for the tag-race / concurrent-runner guard (W1.6, spec §4.6).

The remote is a local bare repository: `git ls-remote` exercises the real
transport code path with no network. Drift cases: planned tag already on the
remote (TAG_RACE), remote branch moved since planning (CONCURRENT_RUNNER),
remote state unconfirmable (TAG_RACE_UNCONFIRMED, fail closed).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from semantic_release.bsr.tag_race import (
    TagRaceGuardError,
    _parse_ls_remote,
    check_tag_race,
)

_AUTHOR = Actor("demo", "demo@example.com")
_DATE = "2024-01-15T12:00:00 +0000"


def _commit(repo: Repo, message: str, filename: str) -> str:
    working = Path(str(repo.working_dir))
    (working / filename).parent.mkdir(parents=True, exist_ok=True)
    (working / filename).write_text(f"{message}\n", encoding="utf-8")
    repo.index.add([filename])
    commit = repo.index.commit(
        message,
        author=_AUTHOR,
        committer=_AUTHOR,
        author_date=_DATE,
        commit_date=_DATE,
    )
    return str(commit.hexsha)


@pytest.fixture
def workspace(tmp_path: Path) -> Repo:
    """A working repo whose 'origin' is a local bare remote; branch pushed."""
    bare_path = tmp_path / "remote.git"
    Repo.init(str(bare_path), bare=True)
    work = Repo.init(tmp_path / "work", initial_branch="main")
    work.create_remote("origin", str(bare_path))
    _commit(work, "feat: initial", "a.txt")
    work.git.push("origin", "main")
    return work


def test_parse_ls_remote_drops_peel_lines() -> None:
    refs = _parse_ls_remote(
        "abc123\trefs/heads/main\n"
        "def456\trefs/tags/v1.0.0\n"
        "def456\trefs/tags/v1.0.0^{}\n"
    )
    assert refs == {"refs/heads/main": "abc123", "refs/tags/v1.0.0": "def456"}


def test_clean_remote_state_passes(workspace: Repo) -> None:
    work = workspace
    pushed_sha = str(work.head.commit.hexsha)
    head = _commit(work, "feat: pending", "b.txt")
    assert head != pushed_sha
    check_tag_race(
        str(work.working_dir),  # type: ignore[arg-type]
        remote_name="origin",
        branch="main",
        planned_tag="v0.2.0",
        planned_head_sha=pushed_sha,
    )


def test_existing_remote_tag_raises_tag_race(workspace: tuple[Repo, Repo]) -> None:
    work = workspace
    head = _commit(work, "feat: pending", "b.txt")
    work.create_tag("v0.2.0")
    work.git.push("origin", "v0.2.0")
    work.git.tag("-d", "v0.2.0")

    with pytest.raises(TagRaceGuardError) as excinfo:
        check_tag_race(
            str(work.working_dir),  # type: ignore[arg-type]
            remote_name="origin",
            branch="main",
            planned_tag="v0.2.0",
            planned_head_sha=head,
        )
    assert excinfo.value.code == "TAG_RACE"
    assert "already exists" in excinfo.value.message


def test_moved_remote_branch_raises_concurrent_runner(
    workspace: tuple[Repo, Repo],
) -> None:
    work = workspace
    head = _commit(work, "feat: pending", "b.txt")
    # Another runner pushes an unrelated commit to the same branch.
    _commit(work, "feat: concurrent", "c.txt")
    work.git.push("origin", "main")
    concurrent_sha = str(work.head.commit.hexsha)
    _commit(work, "feat: local continues", "d.txt")

    with pytest.raises(TagRaceGuardError) as excinfo:
        check_tag_race(
            str(work.working_dir),  # type: ignore[arg-type]
            remote_name="origin",
            branch="main",
            planned_tag="v0.2.0",
            planned_head_sha=head,
        )
    assert excinfo.value.code == "CONCURRENT_RUNNER"
    assert concurrent_sha in excinfo.value.message


def test_unreachable_remote_fails_closed(workspace: tuple[Repo, Repo]) -> None:
    work = workspace
    head = _commit(work, "feat: pending", "b.txt")
    with pytest.raises(TagRaceGuardError) as excinfo:
        check_tag_race(
            str(work.working_dir),  # type: ignore[arg-type]
            remote_name="nonexistent-remote",
            branch="main",
            planned_tag="v0.2.0",
            planned_head_sha=head,
        )
    assert excinfo.value.code == "TAG_RACE_UNCONFIRMED"


def test_missing_remote_branch_is_not_drift(workspace: tuple[Repo, Repo]) -> None:
    """A first release against an empty remote has nothing to race with."""
    work = workspace
    head = _commit(work, "feat: pending", "b.txt")
    check_tag_race(
        str(work.working_dir),  # type: ignore[arg-type]
        remote_name="origin",
        branch="unpushed-branch",
        planned_tag="v0.2.0",
        planned_head_sha=head,
    )


def test_noop_skips_the_check(workspace: tuple[Repo, Repo]) -> None:
    work = workspace
    head = _commit(work, "feat: pending", "b.txt")
    work.create_tag("v0.2.0")
    work.git.push("origin", "v0.2.0")
    check_tag_race(
        str(work.working_dir),  # type: ignore[arg-type]
        remote_name="origin",
        branch="main",
        planned_tag="v0.2.0",
        planned_head_sha=head,
        noop=True,
    )
