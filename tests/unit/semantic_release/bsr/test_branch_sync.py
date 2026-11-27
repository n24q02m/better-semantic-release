"""
Unit tests for the branch-sync check (`check_branch_sync`).

The remote is a local bare repository, so `git fetch` runs the real transport
code path with no network. Every state the check can report is covered: in
sync, behind, ahead, diverged, the offline (no fetch) variants, and the
situations where no comparison is possible (no remote, no upstream, detached
HEAD, unborn branch, not a repository, shallow clone, unreachable remote).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from semantic_release.bsr import doctor as doctor_module
from semantic_release.bsr.doctor import (
    SEVERITY_BLOCKER,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIP,
    _mask_credentials,
    check_branch_sync,
)

_AUTHOR = Actor("demo", "demo@example.com")


@pytest.fixture(autouse=True)
def _local_transport_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # git refuses every transport but local files: no test can reach a network.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")


def _commit(repo: Repo, message: str, filename: str) -> str:
    working = Path(str(repo.working_dir))
    (working / filename).parent.mkdir(parents=True, exist_ok=True)
    (working / filename).write_text(f"{message}\n", encoding="utf-8")
    repo.index.add([filename])
    commit = repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)
    return str(commit.hexsha)


def _advance_remote(bare: Path, scratch: Path, filename: str = "remote.txt") -> str:
    """Push one new commit to the bare remote from an independent clone."""
    other = Repo.clone_from(str(bare), str(scratch))
    sha = _commit(other, "feat: change from another clone", filename)
    other.git.push("origin", "main")
    return sha


def _tracking_sha(repo: Repo, ref: str = "refs/remotes/origin/main") -> str:
    return str(repo.git.rev_parse(ref))


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    Repo.init(str(path), bare=True, initial_branch="main")
    return path


@pytest.fixture
def work(tmp_path: Path, bare: Path) -> Repo:
    """A working repo tracking `origin/main` on a local bare remote, in sync."""
    repo = Repo.init(tmp_path / "work", initial_branch="main")
    repo.create_remote("origin", str(bare))
    _commit(repo, "feat: initial", "a.txt")
    repo.git.push("--set-upstream", "origin", "main")
    return repo


def _check(repo: Repo, **kwargs: object):
    return check_branch_sync(Path(str(repo.working_dir)), **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# comparable states


def test_in_sync_passes(work: Repo) -> None:
    result = _check(work)
    assert result.code == "BRANCH_SYNC"
    assert result.severity == SEVERITY_BLOCKER
    assert result.status == STATUS_PASS
    assert "'main'" in result.what
    assert "'origin/main'" in result.what
    assert "stale" not in result.what


def test_behind_fails_after_fetch(work: Repo, bare: Path, tmp_path: Path) -> None:
    remote_sha = _advance_remote(bare, tmp_path / "other")
    result = _check(work)
    assert result.code == "BRANCH_BEHIND"
    assert result.severity == SEVERITY_BLOCKER
    assert result.status == STATUS_FAIL
    assert "1 commit(s) behind 'origin/main'" in result.what
    assert result.fix == "git pull --ff-only"
    # The fetch refreshed the remote-tracking ref and nothing else.
    assert _tracking_sha(work) == remote_sha
    assert str(work.head.commit.hexsha) != remote_sha


def test_ahead_fails_with_push_fix(work: Repo) -> None:
    _commit(work, "feat: local only", "b.txt")
    _commit(work, "fix: local only too", "c.txt")
    result = _check(work)
    assert result.code == "BRANCH_AHEAD"
    assert result.status == STATUS_FAIL
    assert result.severity == SEVERITY_BLOCKER
    assert "2 commit(s) ahead of 'origin/main'" in result.what
    assert "not on the remote" in result.why
    assert result.fix == "git push origin HEAD:main"


def test_diverged_fails_with_rebase_fix(work: Repo, bare: Path, tmp_path: Path) -> None:
    _advance_remote(bare, tmp_path / "other")
    _commit(work, "feat: local only", "b.txt")
    result = _check(work)
    assert result.code == "BRANCH_DIVERGED"
    assert result.status == STATUS_FAIL
    assert "1 ahead and 1 behind 'origin/main'" in result.what
    assert result.fix == "git pull --rebase"


def test_upstream_with_a_different_remote_branch_name(work: Repo) -> None:
    work.git.push("origin", "main:release")
    work.git.branch("--set-upstream-to=origin/release")
    _commit(work, "feat: local only", "b.txt")
    result = _check(work)
    assert result.code == "BRANCH_AHEAD"
    assert "'origin/release'" in result.what
    assert result.fix == "git push origin HEAD:release"


def test_local_branch_upstream_is_compared_without_fetch(work: Repo) -> None:
    work.git.branch("base")
    work.git.branch("--set-upstream-to=base")
    # A broken remote proves no fetch is attempted for a local upstream.
    work.remotes.origin.set_url(str(Path(str(work.working_dir)).parent / "gone.git"))
    _commit(work, "feat: local only", "b.txt")
    result = _check(work)
    assert result.code == "BRANCH_AHEAD"
    assert "'base'" in result.what
    assert "stale" not in result.why
    assert result.fix == "git push . HEAD:base"


# ---------------------------------------------------------------------------
# offline: never fetch, compare the local tracking ref, say it may be stale.
# A match against an unrefreshed ref is a skip (unconfirmed), not a pass.


def test_offline_never_fetches_and_flags_staleness(
    work: Repo, bare: Path, tmp_path: Path
) -> None:
    before = _tracking_sha(work)
    _advance_remote(bare, tmp_path / "other")
    result = _check(work, offline=True)
    assert result.status == STATUS_SKIP
    assert result.code == "BRANCH_SYNC"
    assert "matches the local tracking ref of 'origin/main'" in result.what
    assert "not fetched" in result.why
    assert "may be stale" in result.why
    assert _tracking_sha(work) == before

    online = _check(work)
    assert online.code == "BRANCH_BEHIND"


def test_offline_does_not_touch_an_unreachable_remote(work: Repo) -> None:
    work.remotes.origin.set_url(str(Path(str(work.working_dir)).parent / "gone.git"))
    result = _check(work, offline=True)
    assert result.code == "BRANCH_SYNC"
    assert result.status == STATUS_SKIP


def test_offline_behind_uses_the_local_tracking_ref(work: Repo) -> None:
    _commit(work, "feat: second", "b.txt")
    work.git.push("origin", "main")
    work.git.reset("--hard", "HEAD~1")
    result = _check(work, offline=True)
    assert result.code == "BRANCH_BEHIND"
    assert result.status == STATUS_FAIL
    assert "may be stale" in result.why


def test_missing_tracking_ref_offline_skips(work: Repo) -> None:
    work.git.update_ref("-d", "refs/remotes/origin/main")
    result = _check(work, offline=True)
    assert result.code == "BRANCH_SYNC"
    assert result.status == STATUS_SKIP
    assert "'origin/main'" in result.what
    assert result.fix == "git fetch origin"


def test_missing_tracking_ref_is_restored_by_the_fetch(work: Repo) -> None:
    work.git.update_ref("-d", "refs/remotes/origin/main")
    result = _check(work)
    assert result.status == STATUS_PASS


# ---------------------------------------------------------------------------
# remote unreachable: fail closed


def test_fetch_failure_fails_closed(work: Repo) -> None:
    gone = str(Path(str(work.working_dir)).parent / "gone.git")
    work.remotes.origin.set_url(gone)
    result = _check(work)
    assert result.code == "BRANCH_SYNC_UNCONFIRMED"
    assert result.status == STATUS_FAIL
    assert result.severity == SEVERITY_BLOCKER
    assert result.why
    assert "--offline" in result.fix


def test_fetch_url_matching_the_remote_is_not_retried(work: Repo) -> None:
    gone = str(Path(str(work.working_dir)).parent / "gone.git")
    work.remotes.origin.set_url(gone)
    result = _check(work, fetch_url=gone)
    assert result.code == "BRANCH_SYNC_UNCONFIRMED"


def test_fetch_url_is_the_fallback_when_the_remote_fetch_fails(
    work: Repo, bare: Path, tmp_path: Path
) -> None:
    remote_sha = _advance_remote(bare, tmp_path / "other")
    work.remotes.origin.set_url(str(tmp_path / "gone.git"))
    result = _check(work, fetch_url=str(bare))
    assert result.code == "BRANCH_BEHIND"
    assert _tracking_sha(work) == remote_sha


def test_fetch_failure_masks_url_credentials(
    work: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_fetch(_repo: Repo, _source: str, _refspec: str) -> tuple[int, str]:
        return (
            128,
            "fatal: unable to access "
            "'https://placeholder-user:placeholder-token@example.invalid/x.git/':\n"
            "Could not resolve host: example.invalid\n",
        )

    monkeypatch.setattr(doctor_module, "_fetch", fake_fetch)
    result = _check(work, fetch_url="https://placeholder-token@example.invalid/x.git")
    assert result.code == "BRANCH_SYNC_UNCONFIRMED"
    assert "placeholder-token" not in result.why
    assert "placeholder-token" not in result.what
    assert "https://****@example.invalid/x.git/" in result.why


def test_rev_list_failure_fails_closed(
    work: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit(work, "feat: local only", "b.txt")

    def fake_counts(_repo: Repo, _head: str, _upstream: str) -> tuple[int, int] | None:
        return None

    monkeypatch.setattr(doctor_module, "_ahead_behind", fake_counts)
    result = _check(work)
    assert result.code == "BRANCH_SYNC_UNCONFIRMED"
    assert result.status == STATUS_FAIL


def test_mask_credentials_hides_userinfo_only() -> None:
    masked = _mask_credentials(
        "fatal: 'https://placeholder-user:placeholder-token@example.invalid/a.git' "
        "and ssh://git@example.invalid/b.git and /plain/path"
    )
    assert "placeholder" not in masked
    assert "https://****@example.invalid/a.git" in masked
    assert "ssh://****@example.invalid/b.git" in masked
    assert "/plain/path" in masked


# ---------------------------------------------------------------------------
# no comparison possible: never crash, report a clear skip


def test_no_remote_skips(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path / "solo", initial_branch="main")
    _commit(repo, "feat: initial", "a.txt")
    result = _check(repo)
    assert result.code == "BRANCH_SYNC"
    assert result.status == STATUS_SKIP
    assert "no git remote" in result.what


def test_no_upstream_skips_with_set_upstream_fix(tmp_path: Path, bare: Path) -> None:
    repo = Repo.init(tmp_path / "untracked", initial_branch="main")
    repo.create_remote("origin", str(bare))
    _commit(repo, "feat: initial", "a.txt")
    repo.git.push("origin", "main")
    result = _check(repo)
    assert result.code == "BRANCH_SYNC"
    assert result.status == STATUS_SKIP
    assert "no upstream" in result.what
    assert result.fix == "git branch --set-upstream-to=origin/main"


def test_detached_head_skips(work: Repo) -> None:
    work.git.checkout("--detach")
    result = _check(work)
    assert result.status == STATUS_SKIP
    assert "detached" in result.what


def test_unborn_branch_skips(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path / "empty", initial_branch="main")
    result = _check(repo)
    assert result.status == STATUS_SKIP
    assert "no commits" in result.what


def test_not_a_git_repository_skips(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = check_branch_sync(plain)
    assert result.code == "BRANCH_SYNC"
    assert result.status == STATUS_SKIP
    assert "not a git repository" in result.what


def test_missing_directory_skips(tmp_path: Path) -> None:
    result = check_branch_sync(tmp_path / "does-not-exist")
    assert result.status == STATUS_SKIP
    assert "not a git repository" in result.what


# ---------------------------------------------------------------------------
# shallow clones


def _shallow_clone(bare: Path, dest: Path) -> Repo:
    return Repo.clone_from(bare.as_uri(), str(dest), depth=1)


def test_shallow_clone_in_sync_passes(work: Repo, bare: Path, tmp_path: Path) -> None:
    _commit(work, "feat: second", "b.txt")
    work.git.push("origin", "main")
    shallow = _shallow_clone(bare, tmp_path / "shallow")
    assert shallow.git.rev_parse("--is-shallow-repository") == "true"
    result = _check(shallow)
    assert result.status == STATUS_PASS


def test_shallow_clone_out_of_sync_fails_and_says_counts_may_be_partial(
    work: Repo, bare: Path, tmp_path: Path
) -> None:
    _commit(work, "feat: second", "b.txt")
    work.git.push("origin", "main")
    shallow = _shallow_clone(bare, tmp_path / "shallow")
    _advance_remote(bare, tmp_path / "other")
    result = _check(shallow)
    assert result.code == "BRANCH_BEHIND"
    assert result.status == STATUS_FAIL
    assert "shallow clone" in result.why
    assert "git fetch --unshallow" in result.why
