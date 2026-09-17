"""
Shared read-only release-state computation (`bsr.preflight`).

Task 2 built the state pipeline inline in the `plan` command; Task 3 needs the
SAME computation for `verify` and `doctor`. One seam, three consumers — the
commands must never drift from each other on what a plain `version` dispatch
would do.

Everything here is read-only: no tag, no commit, no push, no file write.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from git import Repo

if TYPE_CHECKING:
    from semantic_release.bsr.explain import ReleaseDecision

from semantic_release.bsr.explain import BumpStats, classify_no_release
from semantic_release.bsr.guards import is_orphaned_recompute
from semantic_release.bsr.path_filter import make_path_filter
from semantic_release.bsr.summary import build_summary, resolve_components
from semantic_release.cli.commands.version import (
    is_forced_prerelease,
    last_released,
)
from semantic_release.version.algorithm import next_version, tags_and_versions

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from semantic_release.bsr.config import BsrConfig
    from semantic_release.bsr.summary import ComponentPlan
    from semantic_release.cli.config import RawConfig, RuntimeContext
    from semantic_release.enums import LevelBump
    from semantic_release.version.version import Version


@dataclass(frozen=True)
class ReleaseState:
    """What a plain `version` dispatch would do at the current repo state."""

    previous_version: str | None
    new_version: Version
    bump_stats: BumpStats | None
    decision: ReleaseDecision | None
    components: tuple[ComponentPlan, ...]

    @property
    def would_release(self) -> bool:
        """True when a plain dispatch would perform a release."""
        return self.decision is None


def compute_release_state(
    *,
    runtime: RuntimeContext,
    config: RawConfig,
    bsr_cfg: BsrConfig,
) -> ReleaseState:
    """
    Compute the release state read-only.

    Uses the SAME algorithm and inputs as the `version` command's non-forced
    path, minus every persistence step that follows it there. `config` is the
    raw CLI config (only `repo_dir` and `tag_format` are read from it).
    """
    repo_dir: Path = runtime.repo_dir
    translator = runtime.version_translator
    parser = runtime.commit_parser

    _bsr_path_filter = make_path_filter(bsr_cfg, os.getcwd())
    prerelease = is_forced_prerelease(
        as_prerelease=False, forced_level_bump=None, prerelease=runtime.prerelease
    )

    previous_version: str | None = None
    last_release = last_released(config.repo_dir, tag_format=config.tag_format)
    if last_release is not None:
        previous_version = str(last_release[1])

    _bump_stats_box: list[BumpStats] = []

    def _stash_bump_stats(
        level_bump: LevelBump,
        commit_count: int,
        latest_version: Version,
        type_counts: Mapping,
    ) -> None:
        _bump_stats_box.append(
            BumpStats(
                level_bump=level_bump,
                commit_count=commit_count,
                latest_version=latest_version,
                type_counts=dict(type_counts),
            )
        )

    new_version = next_version(
        repo=Repo(str(repo_dir)),
        translator=translator,
        commit_parser=parser,
        prerelease=prerelease,
        major_on_zero=runtime.major_on_zero,
        allow_zero_version=runtime.allow_zero_version,
        commit_path_filter=_bsr_path_filter,
        bump_stats_sink=_stash_bump_stats,
    )

    # Already-released? Same classification as `version`, recorded as a
    # decision instead of an early exit.
    decision: ReleaseDecision | None = None
    with Repo(str(repo_dir)) as git_repo:
        previously_released_versions = {
            v for _, v in tags_and_versions(list(git_repo.tags), translator)
        }
    if new_version in previously_released_versions:
        decision = classify_no_release(
            is_orphaned=bool(is_orphaned_recompute(repo_dir, translator, new_version)),
            bump_stats=_bump_stats_box[0] if _bump_stats_box else None,
        )

    components: tuple[ComponentPlan, ...] = ()
    if bsr_cfg.summary:
        resolved = resolve_components(
            bsr_cfg,
            default_name=runtime.project_metadata.get("name", "") or "(repo)",
        )
        with Repo(str(repo_dir)) as summary_repo:
            components = build_summary(
                resolved,
                repo=summary_repo,
                translator=translator,
                commit_parser=parser,
                prerelease=prerelease,
                major_on_zero=runtime.major_on_zero,
                allow_zero_version=runtime.allow_zero_version,
                component_path_map=bsr_cfg.component_path_map,
            )

    return ReleaseState(
        previous_version=previous_version,
        new_version=new_version,
        bump_stats=_bump_stats_box[0] if _bump_stats_box else None,
        decision=decision,
        components=components,
    )
