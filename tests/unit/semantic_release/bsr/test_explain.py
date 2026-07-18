from __future__ import annotations

from semantic_release.bsr.explain import (
    ALREADY_RELEASED_NOOP,
    NO_QUALIFYING_COMMITS,
    ORPHAN,
    BumpStats,
    classify_no_release,
    format_no_release_reason,
    format_why_this_bump,
)
from semantic_release.enums import LevelBump
from semantic_release.version.version import Version

_V1 = Version.parse("1.0.0")


def _bump_stats(
    *,
    level_bump: LevelBump = LevelBump.NO_RELEASE,
    commit_count: int = 0,
    type_counts: dict[str, int] | None = None,
) -> BumpStats:
    return BumpStats(
        level_bump=level_bump,
        commit_count=commit_count,
        latest_version=_V1,
        type_counts=type_counts or {},
    )


class TestClassifyNoRelease:
    def test_orphan_takes_priority_over_bump_stats(self) -> None:
        decision = classify_no_release(
            is_orphaned=True,
            bump_stats=_bump_stats(commit_count=5),
        )
        assert decision.reason == ORPHAN

    def test_orphan_with_no_bump_stats_available(self) -> None:
        """Forced-level-bump path has no bump_stats -- orphan must still classify."""
        decision = classify_no_release(is_orphaned=True, bump_stats=None)
        assert decision.reason == ORPHAN
        assert decision.commit_count == 0

    def test_zero_commits_since_last_release_is_noop(self) -> None:
        decision = classify_no_release(
            is_orphaned=False,
            bump_stats=_bump_stats(commit_count=0),
        )
        assert decision.reason == ALREADY_RELEASED_NOOP
        assert decision.commit_count == 0

    def test_commits_scanned_but_none_qualifying(self) -> None:
        decision = classify_no_release(
            is_orphaned=False,
            bump_stats=_bump_stats(commit_count=4, level_bump=LevelBump.NO_RELEASE),
        )
        assert decision.reason == NO_QUALIFYING_COMMITS
        assert decision.commit_count == 4

    def test_missing_bump_stats_defaults_to_noop(self) -> None:
        """No bump_stats (forced-level path) and not orphaned -> benign no-op."""
        decision = classify_no_release(is_orphaned=False, bump_stats=None)
        assert decision.reason == ALREADY_RELEASED_NOOP
        assert decision.commit_count == 0


class TestFormatNoReleaseReason:
    def test_no_qualifying_commits_mentions_count_and_not_misattribution(self) -> None:
        decision = classify_no_release(
            is_orphaned=False,
            bump_stats=_bump_stats(commit_count=3),
        )
        msg = format_no_release_reason(decision, _V1)
        assert "NO_QUALIFYING_COMMITS" in msg
        assert "3" in msg
        # The core bug this fixes: PSR's stock message blames an already-
        # released tag when the real cause is zero releasable commits.
        assert "has already been released" not in msg

    def test_orphan_reason_text(self) -> None:
        decision = classify_no_release(is_orphaned=True, bump_stats=None)
        msg = format_no_release_reason(decision, _V1)
        assert "ORPHAN" in msg
        assert str(_V1) in msg

    def test_already_released_noop_reason_text(self) -> None:
        decision = classify_no_release(
            is_orphaned=False, bump_stats=_bump_stats(commit_count=0)
        )
        msg = format_no_release_reason(decision, _V1)
        assert "ALREADY_RELEASED_NOOP" in msg
        assert str(_V1) in msg


class TestFormatWhyThisBump:
    def test_includes_level_and_commit_count(self) -> None:
        stats = _bump_stats(
            level_bump=LevelBump.MINOR,
            commit_count=4,
            type_counts={"feat": 3, "fix": 1},
        )
        msg = format_why_this_bump(stats)
        assert "minor" in msg
        assert "4" in msg
        assert "3 feat" in msg
        assert "1 fix" in msg
        assert str(_V1) in msg

    def test_handles_empty_type_counts(self) -> None:
        stats = _bump_stats(level_bump=LevelBump.PATCH, commit_count=1, type_counts={})
        msg = format_why_this_bump(stats)
        assert "patch" in msg
        assert str(_V1) in msg
