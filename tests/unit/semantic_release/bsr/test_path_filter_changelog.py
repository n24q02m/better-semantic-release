from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.bsr.path_filter import filter_commits_by_paths
from semantic_release.changelog.release_history import ReleaseHistory
from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.version.translator import VersionTranslator

if TYPE_CHECKING:
    from typing import Callable, Sequence

    from git.objects.commit import Commit

_AUTHOR = Actor("t", "t@t")


def _commit(repo: Repo, relpath: str, content: str, message: str) -> Commit:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _from_git_history(
    repo: Repo,
    commit_path_filter: Callable[[Sequence[Commit]], Sequence[Commit]] | None = None,
) -> ReleaseHistory:
    return ReleaseHistory.from_git_history(
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        commit_path_filter=commit_path_filter,
    )


def _unreleased_feature_hashes(rh: ReleaseHistory) -> set[str]:
    return {parsed.commit.hexsha for parsed in rh.unreleased.get("features", [])}


def test_from_git_history_omitted_filter_matches_explicit_none(tmp_path: Path) -> None:
    """Omitting `commit_path_filter` behaves identically to passing `None` -- drop-in parity."""
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    _commit(repo, "apps/web/y.py", "y", "feat: web")
    assert _unreleased_feature_hashes(_from_git_history(repo)) == (
        _unreleased_feature_hashes(_from_git_history(repo, commit_path_filter=None))
    )


def test_from_git_history_filter_excludes_non_touching_commits(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    _commit(repo, "apps/web/y.py", "y", "feat: web")

    rh = _from_git_history(
        repo, commit_path_filter=lambda commits: filter_commits_by_paths(commits, ("apps/api",))
    )
    assert _unreleased_feature_hashes(rh) == {c1.hexsha}


def test_from_git_history_filter_keeping_all_matches_baseline(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    _commit(repo, "apps/web/y.py", "y", "feat: web")

    baseline = _unreleased_feature_hashes(_from_git_history(repo))
    filtered = _unreleased_feature_hashes(_from_git_history(repo, commit_path_filter=list))
    assert filtered == baseline
    assert len(baseline) == 2
