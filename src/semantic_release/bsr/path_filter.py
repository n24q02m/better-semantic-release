from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import NULL_TREE, Repo

if TYPE_CHECKING:
    import os
    from typing import Callable, Sequence

    from git.objects.commit import Commit

    from semantic_release.bsr.config import BsrConfig


def _normalize_prefix(prefix: str) -> str:
    return prefix.replace("\\", "/").strip("/")


def _path_under_prefix(changed_path: str, prefix: str) -> bool:
    if not prefix:
        return True
    return changed_path == prefix or changed_path.startswith(f"{prefix}/")


def commit_touches_paths(commit: Commit, paths: tuple[str, ...]) -> bool:
    """
    True if any file changed in `commit` is under any prefix in `paths`.

    Path matching is POSIX-normalised, segment-prefix (`apps/api` matches
    `apps/api/x.py` but not `apps/api-worker/y.py`). A root commit (no
    parents) is diffed against the empty tree. Any other commit -- including
    a merge -- is diffed against its first parent: for a merge, this
    correctly excludes a merge that introduced no change under `paths` even
    when its underlying feature commits (traversed and matched separately by
    the DFS) did touch other paths.

    `paths` empty returns False -- the no-op/passthrough case is handled by
    `filter_commits_by_paths`, which never delegates to this function then.
    """
    if not paths:
        return False

    comparand = commit.parents[0] if commit.parents else NULL_TREE
    diff_index = commit.diff(comparand)
    changed_paths = {
        changed_path
        for diff in diff_index
        for changed_path in (diff.a_path, diff.b_path)
        if changed_path is not None
    }

    normalized_paths = tuple(_normalize_prefix(path) for path in paths)
    return any(
        _path_under_prefix(changed_path, prefix)
        for changed_path in changed_paths
        for prefix in normalized_paths
    )


def filter_commits_by_paths(
    commits: Sequence[Commit], paths: tuple[str, ...]
) -> list[Commit]:
    """Keep only commits touching `paths`. Empty `paths` is a no-op passthrough."""
    if not paths:
        return list(commits)
    return [commit for commit in commits if commit_touches_paths(commit, paths)]


def _default_paths_from_repo_dir(
    repo_dir: str | os.PathLike[str],
) -> tuple[str, ...]:
    """
    Resolve the default filter paths from the run directory.

    Walks up from `repo_dir` to find the git repo root, then returns
    `repo_dir` expressed as a single POSIX path relative to that root. When
    `repo_dir` IS the repo root, the relative path is `.`, which resolves to
    an empty tuple -- "everything", i.e. no-op filter (matches
    `filter_commits_by_paths`'s empty-paths passthrough).
    """
    resolved_repo_dir = Path(repo_dir).resolve()
    with Repo(str(resolved_repo_dir), search_parent_directories=True) as repo:
        working_tree_dir = repo.working_tree_dir or repo.working_dir

    if working_tree_dir is None:
        return ()

    root = Path(working_tree_dir).resolve()
    rel = resolved_repo_dir.relative_to(root).as_posix()
    return () if rel == "." else (rel,)


def make_path_filter(
    bsr_config: BsrConfig, repo_dir: str | os.PathLike[str]
) -> Callable[[Sequence[Commit]], list[Commit]] | None:
    """
    Build the commit path-filter closure from resolved bsr config.

    Returns None when `bsr_config.path_filter` is off -- the drop-in default:
    both injection seams (`next_version`, `ReleaseHistory.from_git_history`)
    treat None as "no filter", so this keeps stock PSR behaviour identical.
    When on, resolves `bsr_config.paths` if non-empty, else defaults to the
    run directory (`repo_dir`) relative to the git repo root.
    """
    if not bsr_config.path_filter:
        return None

    paths = bsr_config.paths or _default_paths_from_repo_dir(repo_dir)

    return lambda commits: filter_commits_by_paths(commits, paths)
