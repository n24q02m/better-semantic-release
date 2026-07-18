from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.bsr.config import BsrConfig
from semantic_release.bsr.path_filter import (
    commit_touches_paths,
    filter_commits_by_paths,
    make_path_filter,
)

if TYPE_CHECKING:
    from git.objects.commit import Commit

_AUTHOR = Actor("t", "t@t")


def _commit(repo: Repo, relpath: str, content: str, message: str) -> Commit:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _rename(repo: Repo, old_relpath: str, new_relpath: str, message: str) -> Commit:
    old_path = Path(str(repo.working_tree_dir)) / old_relpath
    new_path = Path(str(repo.working_tree_dir)) / new_relpath
    new_path.parent.mkdir(parents=True, exist_ok=True)
    repo.index.move([str(old_path), str(new_path)])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


# --- commit_touches_paths -----------------------------------------------


def test_commit_touches_paths_true_for_matching_file(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    assert commit_touches_paths(c1, ("apps/api",)) is True


def test_commit_touches_paths_false_for_non_matching_file(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    assert commit_touches_paths(c1, ("apps/web",)) is False


def test_commit_touches_paths_root_commit_diffs_against_empty_tree(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: initial api")
    assert len(c1.parents) == 0
    assert commit_touches_paths(c1, ("apps/api",)) is True
    assert commit_touches_paths(c1, ("apps/web",)) is False


def test_commit_touches_paths_prefix_boundary_api_vs_api_worker(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/api-worker/z.py", "z", "feat: api-worker")
    # A commit touching apps/api-worker must NOT match the apps/api prefix.
    assert commit_touches_paths(c2, ("apps/api",)) is False
    assert commit_touches_paths(c2, ("apps/api-worker",)) is True


def test_commit_touches_paths_empty_paths_returns_false(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    assert commit_touches_paths(c1, ()) is False


def test_commit_touches_paths_rename_into_path_counts(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "scratch/x.py", "x", "feat: scratch")
    c2 = _rename(repo, "scratch/x.py", "apps/api/x.py", "feat: move into api")
    assert commit_touches_paths(c2, ("apps/api",)) is True


def test_commit_touches_paths_merge_diffs_against_first_parent(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    main = repo.active_branch
    feature = repo.create_head("feature", main.commit)
    repo.head.reference = feature
    repo.head.reset(index=True, working_tree=True)
    feature_commit = _commit(repo, "libs/x/z.py", "z", "feat: libs")
    repo.head.reference = main
    repo.head.reset(index=True, working_tree=True)
    merge_base = repo.merge_base(main.commit, feature_commit)
    repo.index.merge_tree(feature_commit, base=merge_base)
    merge_commit = repo.index.commit(
        "merge feature",
        parent_commits=(main.commit, feature_commit),
        author=_AUTHOR,
        committer=_AUTHOR,
    )
    assert len(merge_commit.parents) > 1
    # The merge's own diff (vs its first parent) only shows the libs/x change
    # introduced by the merged branch -- so a filter on apps/api must exclude
    # it (the underlying feature commit is traversed -- and matched -- on its
    # own by the DFS), while a filter on libs/x must match it.
    assert commit_touches_paths(merge_commit, ("apps/api",)) is False
    assert commit_touches_paths(merge_commit, ("libs/x",)) is True


# --- filter_commits_by_paths ---------------------------------------------


def test_filter_commits_by_paths_keeps_only_touching_commits(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/web/y.py", "y", "feat: web")
    c3 = _commit(repo, "libs/x/z.py", "z", "feat: libs")
    assert filter_commits_by_paths([c1, c2, c3], ("apps/api",)) == [c1]


def test_filter_commits_by_paths_multi_path(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/web/y.py", "y", "feat: web")
    c3 = _commit(repo, "libs/x/z.py", "z", "feat: libs")
    assert filter_commits_by_paths([c1, c2, c3], ("apps/api", "libs/x")) == [c1, c3]


def test_filter_commits_by_paths_empty_paths_is_passthrough(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/web/y.py", "y", "feat: web")
    assert filter_commits_by_paths([c1, c2], ()) == [c1, c2]


def test_filter_commits_by_paths_merge_with_no_own_change_excluded(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    main = repo.active_branch
    feature = repo.create_head("feature", main.commit)
    repo.head.reference = feature
    repo.head.reset(index=True, working_tree=True)
    feature_commit = _commit(repo, "libs/x/z.py", "z", "feat: libs")
    repo.head.reference = main
    repo.head.reset(index=True, working_tree=True)
    merge_base = repo.merge_base(main.commit, feature_commit)
    repo.index.merge_tree(feature_commit, base=merge_base)
    merge_commit = repo.index.commit(
        "merge feature",
        parent_commits=(main.commit, feature_commit),
        author=_AUTHOR,
        committer=_AUTHOR,
    )
    assert filter_commits_by_paths([merge_commit], ("apps/api",)) == []
    assert filter_commits_by_paths([merge_commit], ("libs/x",)) == [merge_commit]


# --- make_path_filter ------------------------------------------------------


def test_make_path_filter_returns_none_when_off(tmp_path: Path) -> None:
    Repo.init(tmp_path)
    cfg = BsrConfig(path_filter=False)
    assert make_path_filter(cfg, tmp_path) is None


def test_make_path_filter_working_closure_with_explicit_paths(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/web/y.py", "y", "feat: web")
    cfg = BsrConfig(path_filter=True, paths=("apps/api",))
    path_filter = make_path_filter(cfg, tmp_path)
    assert path_filter is not None
    assert path_filter([c1, c2]) == [c1]


def test_make_path_filter_run_dir_default_at_repo_root_is_noop(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/web/y.py", "y", "feat: web")
    cfg = BsrConfig(path_filter=True, paths=())
    path_filter = make_path_filter(cfg, tmp_path)
    assert path_filter is not None
    assert path_filter([c1, c2]) == [c1, c2]


def test_make_path_filter_run_dir_default_from_subdirectory(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    c2 = _commit(repo, "apps/web/y.py", "y", "feat: web")
    run_dir = tmp_path / "apps" / "api"
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = BsrConfig(path_filter=True, paths=())
    path_filter = make_path_filter(cfg, run_dir)
    assert path_filter is not None
    assert path_filter([c1, c2]) == [c1]
