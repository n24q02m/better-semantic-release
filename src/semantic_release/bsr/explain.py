"""
better-semantic-release additions (bsr): release-decision explainer (`bsr.explain`).

Fixes PSR's misattributed "no release" message. `version.py:609` prints
"...has already been released!" whenever `next_version()` recomputes an
already-released version, but the REAL cause is usually that zero commits
since the last release qualify for a bump -- that fact is only logged at
INFO by `algorithm.py:399` and never surfaced to the user. This module
classifies the real reason and renders it for stderr.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Mapping

    from semantic_release.enums import LevelBump
    from semantic_release.version.version import Version

NO_QUALIFYING_COMMITS = "NO_QUALIFYING_COMMITS"
ALREADY_RELEASED_NOOP = "ALREADY_RELEASED_NOOP"
ORPHAN = "ORPHAN"


@dataclass(frozen=True)
class BumpStats:
    """
    Cheap stats stashed out of `next_version()` via its optional
    `bump_stats_sink` callback, fired once `level_bump` is known -- covering
    both the "no release" and the "real bump" outcomes with a single seam.
    """

    level_bump: LevelBump
    commit_count: int
    latest_version: Version
    type_counts: Mapping[str, int]


@dataclass(frozen=True)
class ReleaseDecision:
    """The classified reason a release did NOT happen this run."""

    reason: str  # NO_QUALIFYING_COMMITS | ALREADY_RELEASED_NOOP | ORPHAN
    commit_count: int


def classify_no_release(
    *, is_orphaned: bool, bump_stats: BumpStats | None
) -> ReleaseDecision:
    """
    Classify why `new_version` was found already-released.

    `is_orphaned` (the M1 guard's own reachability check) takes priority
    over everything else: it is authoritative regardless of commit counts.
    Otherwise, distinguish by the RAW commit count since the last release
    (before qualification, i.e. `len(commits_since_last_release)` post
    path-filter, pre commit-type parsing): zero means a genuine no-op
    re-dispatch (`ALREADY_RELEASED_NOOP`); more than zero but still no
    releasable bump means every commit since the last release was
    non-qualifying, e.g. chore/docs-only (`NO_QUALIFYING_COMMITS`) -- this is
    the case PSR actively misattributes today.

    `bump_stats` is None on the forced-level-bump CLI path (`--major` /
    `--minor` / `--patch` / `--prerelease`), which never calls `next_version()`
    and so has no commit-scan data; that recomputes without any bump_stats,
    which classifies as a benign no-op unless orphaned.
    """
    if is_orphaned:
        return ReleaseDecision(
            reason=ORPHAN,
            commit_count=bump_stats.commit_count if bump_stats is not None else 0,
        )

    commit_count = bump_stats.commit_count if bump_stats is not None else 0
    reason = NO_QUALIFYING_COMMITS if commit_count > 0 else ALREADY_RELEASED_NOOP
    return ReleaseDecision(reason=reason, commit_count=commit_count)


def format_no_release_reason(decision: ReleaseDecision, new_version: Version) -> str:
    """Render the classified no-release reason for stderr (`bsr.explain`)."""
    if decision.reason == NO_QUALIFYING_COMMITS:
        return (
            "better-semantic-release explain: NO_QUALIFYING_COMMITS -- "
            f"{decision.commit_count} commit(s) scanned since {new_version!s}, 0 were "
            "releasable (no feat/fix/breaking-change commits). No release will be made."
        )
    if decision.reason == ORPHAN:
        return (
            "better-semantic-release explain: ORPHAN -- "
            f"{new_version!s} recomputes to an already-released but unreachable tag "
            "(likely a rebase/force-push dropped the release commit)."
        )
    # ALREADY_RELEASED_NOOP
    return (
        "better-semantic-release explain: ALREADY_RELEASED_NOOP -- "
        f"the current tip is already tagged {new_version!s}; nothing new to release."
    )


def format_why_this_bump(bump_stats: BumpStats) -> str:
    """Render the "why this bump" line for a real release (`bsr.explain`)."""
    type_breakdown = ", ".join(
        f"{count} {commit_type}"
        for commit_type, count in sorted(bump_stats.type_counts.items())
    )
    breakdown_suffix = f" from {type_breakdown} commit(s)" if type_breakdown else ""
    return (
        f"better-semantic-release explain: {bump_stats.level_bump.name.lower()} bump"
        f"{breakdown_suffix} since {bump_stats.latest_version!s} "
        f"({bump_stats.commit_count} commit(s) scanned)."
    )
