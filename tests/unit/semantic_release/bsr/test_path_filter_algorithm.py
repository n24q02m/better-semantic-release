from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.const import DEFAULT_VERSION
from semantic_release.version.algorithm import next_version
from semantic_release.version.translator import VersionTranslator

if TYPE_CHECKING:
    from typing import Callable, Sequence

    from git.objects.commit import Commit

    from semantic_release.version.version import Version

_AUTHOR = Actor("t", "t@t")


def _commit(repo: Repo, relpath: str, content: str, message: str) -> Commit:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _default_initial_version() -> Version | None:
    translator = VersionTranslator()
    return translator.from_tag(translator.str_to_tag(DEFAULT_VERSION))


def _next_version(
    repo: Repo,
    commit_path_filter: Callable[[Sequence[Commit]], Sequence[Commit]] | None = None,
) -> Version:
    return next_version(
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        allow_zero_version=True,
        major_on_zero=True,
        commit_path_filter=commit_path_filter,
    )


def test_next_version_omitted_filter_matches_explicit_none(tmp_path: Path) -> None:
    """Omitting `commit_path_filter` behaves identically to passing `None` -- drop-in parity."""
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: initial api")
    assert _next_version(repo) == _next_version(repo, commit_path_filter=None)


def test_next_version_filter_dropping_all_commits_yields_no_release(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: initial api")

    result = _next_version(repo, commit_path_filter=lambda _commits: [])
    assert result == _default_initial_version()


def test_next_version_filter_keeping_commit_still_bumps(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: initial api")

    baseline = _next_version(repo)
    filtered = _next_version(repo, commit_path_filter=list)
    assert filtered == baseline
    assert filtered != _default_initial_version()
