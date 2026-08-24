"""
better-semantic-release additions (bsr): monorepo release-plan summary
(`bsr.summary`).

Neither upstream's `conventional-monorepo` parser nor M2's single-path commit
filter (`bsr.path_filter`) gives a per-component view of what a release run
WOULD do -- issues #168/#1425/#1073/#1215 all want a "release plan" table
across a monorepo's components. This module builds that table by calling the
SAME `next_version()` algorithm PSR uses for the real release, once per
configured component, scoped via the M2 path-filter
(`bsr.path_filter.filter_commits_by_paths`) closure -- report-only, no side
effects, never persisted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from semantic_release.bsr.component_map import map_paths
from semantic_release.bsr.config import BsrComponent
from semantic_release.bsr.path_filter import filter_commits_by_paths
from semantic_release.version.algorithm import next_version

if TYPE_CHECKING:
    from typing import Callable, Mapping, Sequence

    from git.objects.commit import Commit
    from git.repo.base import Repo

    from semantic_release.bsr.component_map import ComponentPathMap
    from semantic_release.bsr.config import BsrConfig
    from semantic_release.commit_parser import (
        CommitParser,
        ParseResult,
        ParserOptions,
    )
    from semantic_release.enums import LevelBump
    from semantic_release.version.translator import VersionTranslator
    from semantic_release.version.version import Version

_SAMPLE_PATH_LIMIT = 3
_TABLE_HEADERS = (
    "component",
    "would-release",
    "level",
    "commits",
    "sample paths",
    "version",
)


@dataclass(frozen=True)
class ComponentPlan:
    """One row of the monorepo release-plan summary (`bsr.summary`)."""

    name: str
    would_release: bool
    level: str
    commit_count: int
    sample_paths: tuple[str, ...]
    resulting_version: Version


def resolve_components(
    bsr_config: BsrConfig, *, default_name: str
) -> tuple[BsrComponent, ...]:
    """
    Resolve the components to report on.

    Reuses `bsr.components` when configured; otherwise falls back to a
    SINGLE component built from `bsr.paths` (the existing M2 single-path
    filter config), so a project that never configured `components` still
    gets a one-row summary instead of an empty report.
    """
    if bsr_config.component_path_map is not None:
        component_map = bsr_config.component_path_map
        return tuple(
            BsrComponent(
                name=component.component_id,
                paths=tuple(
                    dict.fromkeys(
                        (
                            *component.roots,
                            *(
                                rule.path
                                for rule in component_map.rules
                                if component.component_id in rule.components
                            ),
                        )
                    )
                ),
            )
            for component in component_map.components
        )
    if bsr_config.components:
        return bsr_config.components
    return (BsrComponent(name=default_name, paths=bsr_config.paths),)


def _sample_changed_paths(commits: Sequence[Commit]) -> tuple[str, ...]:
    """First few DISTINCT file paths touched by `commits`, in commit order."""
    seen: list[str] = []
    for commit in commits:
        for changed_path in commit.stats.files:
            path_str = str(changed_path)
            if path_str in seen:
                continue
            seen.append(path_str)
            if len(seen) >= _SAMPLE_PATH_LIMIT:
                return tuple(seen)
    return tuple(seen)


def build_component_plan(
    component: BsrComponent,
    *,
    repo: Repo,
    translator: VersionTranslator,
    commit_parser: CommitParser[ParseResult, ParserOptions],
    prerelease: bool,
    major_on_zero: bool,
    allow_zero_version: bool,
    path_filter: Callable[[Sequence[Commit]], Sequence[Commit]] | None = None,
) -> ComponentPlan:
    """
    Compute one component's release plan.

    Calls the SAME `next_version()` PSR uses for the real release, scoped to
    `component.paths` via the M2 path-filter closure (empty `paths` is a
    no-op passthrough, i.e. "the whole repo") -- report-only, no writes. Both
    `bump_stats_sink` and `commit_path_filter` fire exactly once per
    `next_version()` call (unconditionally, before any early return), so the
    boxes below are always populated.
    """
    matched_commits_box: list[Sequence[Commit]] = []

    def _capturing_filter(commits: Sequence[Commit]) -> Sequence[Commit]:
        matched = (
            path_filter(commits)
            if path_filter is not None
            else filter_commits_by_paths(commits, component.paths)
        )
        matched_commits_box.append(matched)
        return matched

    bump_stats_box: list[tuple[LevelBump, int, Version, Mapping[str, int]]] = []

    def _stash(
        level_bump: LevelBump,
        commit_count: int,
        latest_version: Version,
        type_counts: Mapping[str, int],
    ) -> None:
        bump_stats_box.append((level_bump, commit_count, latest_version, type_counts))

    resulting_version = next_version(
        repo=repo,
        translator=translator,
        commit_parser=commit_parser,
        allow_zero_version=allow_zero_version,
        major_on_zero=major_on_zero,
        prerelease=prerelease,
        commit_path_filter=_capturing_filter,
        bump_stats_sink=_stash,
    )

    level_bump, commit_count, latest_version, _type_counts = bump_stats_box[0]

    return ComponentPlan(
        name=component.name,
        would_release=resulting_version != latest_version,
        level=level_bump.name,
        commit_count=commit_count,
        sample_paths=_sample_changed_paths(matched_commits_box[0]),
        resulting_version=resulting_version,
    )


def _component_map_filter(
    component_name: str, component_map: ComponentPathMap
) -> Callable[[Sequence[Commit]], Sequence[Commit]]:
    def _filter(commits: Sequence[Commit]) -> Sequence[Commit]:
        matched: list[Commit] = []
        for commit in commits:
            changed_paths = tuple(str(path) for path in commit.stats.files)
            if component_name in map_paths(component_map, changed_paths):
                matched.append(commit)
        return matched

    return _filter


def build_summary(
    components: Sequence[BsrComponent],
    *,
    repo: Repo,
    translator: VersionTranslator,
    commit_parser: CommitParser[ParseResult, ParserOptions],
    prerelease: bool,
    major_on_zero: bool,
    allow_zero_version: bool,
    component_path_map: ComponentPathMap | None = None,
) -> tuple[ComponentPlan, ...]:
    """Build the full per-component release plan, one row per `components` entry."""
    return tuple(
        build_component_plan(
            component,
            repo=repo,
            translator=translator,
            commit_parser=commit_parser,
            prerelease=prerelease,
            major_on_zero=major_on_zero,
            allow_zero_version=allow_zero_version,
            path_filter=(
                _component_map_filter(component.name, component_path_map)
                if component_path_map is not None
                else None
            ),
        )
        for component in components
    )


def render_summary_table(plans: Sequence[ComponentPlan]) -> str:
    """Render the per-component release-plan table for stderr (`bsr.summary`)."""
    rows = [
        (
            plan.name,
            "yes" if plan.would_release else "no",
            plan.level,
            str(plan.commit_count),
            ", ".join(plan.sample_paths) or "-",
            str(plan.resulting_version),
        )
        for plan in plans
    ]
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(_TABLE_HEADERS)
    ]

    def _fmt(row: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(row, widths))

    lines = [
        "better-semantic-release summary: monorepo release plan",
        _fmt(_TABLE_HEADERS),
        _fmt(tuple("-" * width for width in widths)),
        *(_fmt(row) for row in rows),
    ]
    return "\n".join(lines)
