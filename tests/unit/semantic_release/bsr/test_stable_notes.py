"""
Unit tests for `bsr.stable_notes.aggregate_stable_release` (C4) -- exercises
the aggregation/de-dup/scope logic directly against hand-built
`ReleaseHistory` objects (no real git repo needed), so the full scope/dedup/
ordering matrix runs fast and deterministically. See test_stable_notes_
changelog.py and test_stable_notes_cli.py for the real end-to-end (git repo
+ rendered changelog / CLI seam) coverage.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from git import Actor

from semantic_release.bsr.stable_notes import (
    SCOPE_LINE,
    SCOPE_SINCE_STABLE,
    aggregate_stable_release,
)
from semantic_release.changelog.release_history import Release, ReleaseHistory
from semantic_release.commit_parser.token import ParsedCommit
from semantic_release.enums import LevelBump
from semantic_release.version.version import Version

if TYPE_CHECKING:
    from semantic_release.commit_parser import ParseResult

_AUTHOR = Actor("t", "t@t")
_BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@dataclass
class _FakeCommit:
    """Duck-types a `git.Commit` far enough for `ParsedCommit.hexsha`."""

    hexsha: str


def _parsed(
    sha: str, commit_type: str = "feature", description: str = "x"
) -> ParsedCommit:
    return ParsedCommit(
        bump=LevelBump.MINOR,
        type=commit_type,
        scope="",
        descriptions=[description],
        breaking_descriptions=[],
        commit=_FakeCommit(hexsha=sha),  # type: ignore[arg-type]
    )


def _release(
    version: Version, elements: dict[str, list[ParseResult]], *, minutes_ago: int
) -> Release:
    return Release(
        tagger=_AUTHOR,
        committer=_AUTHOR,
        tagged_date=_BASE_TIME - timedelta(minutes=minutes_ago),
        elements=defaultdict(list, elements),
        version=version,
    )


def _history(released: dict[Version, Release]) -> ReleaseHistory:
    return ReleaseHistory(unreleased={}, released=released)


def _shas(release: Release, commit_type: str = "feature") -> set[str]:
    return {c.hexsha for c in release["elements"].get(commit_type, [])}


V01 = Version.parse("0.1.0")
V02_BETA1 = Version.parse("0.2.0-beta.1")
V02_BETA2 = Version.parse("0.2.0-beta.2")
V02 = Version.parse("0.2.0")
V10_BETA1 = Version.parse("1.0.0-beta.1")
V10 = Version.parse("1.0.0")


def test_line_scope_merges_all_same_line_prereleases_deduped_and_keeps_own() -> None:
    """
    The core C4 fix: a stable finalize whose OWN bucket is empty (the
    "consumed by prerelease" bug) gets every same-line prerelease's commits
    folded in, plus its own (non-empty here) commits are preserved too.
    """
    c1, c2, c_own = _parsed("c1"), _parsed("c2"), _parsed("cown")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [c1]}, minutes_ago=60),
        V02_BETA2: _release(V02_BETA2, {"feature": [c2]}, minutes_ago=30),
        V02: _release(V02, {"feature": [c_own]}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02, scope=SCOPE_LINE)

    assert _shas(result) == {"c1", "c2", "cown"}
    assert result["version"] == V02
    # release_history itself is untouched -- aggregation returns a NEW Release
    assert _shas(rh.released[V02]) == {"cown"}


def test_line_scope_reproduces_empty_stable_section_bug_when_off() -> None:
    """
    Baseline for the fix above: WITHOUT aggregation, a stable finalize with
    zero brand-new commits since the last prerelease has a genuinely empty
    section (issue #555) -- proving the bug this module fixes is real.
    """
    c1 = _parsed("c1")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [c1]}, minutes_ago=30),
        V02: _release(V02, {}, minutes_ago=0),
    }
    assert _shas(released[V02]) == set()  # the bug, pre-aggregation


def test_since_stable_scope_includes_abandoned_other_line_prerelease() -> None:
    """
    `since_stable` walks back to the last STABLE tag regardless of line, so
    an abandoned different-line prerelease (0.2.0-beta.1, superseded by a
    forced bump to the 1.0.0 line) is still folded in.
    """
    c_abandoned, c_current = _parsed("cA"), _parsed("cB")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [c_abandoned]}, minutes_ago=80),
        V10_BETA1: _release(V10_BETA1, {"feature": [c_current]}, minutes_ago=40),
        V10: _release(V10, {}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V10, scope=SCOPE_SINCE_STABLE)

    assert _shas(result) == {"cA", "cB"}


def test_line_scope_excludes_the_same_abandoned_other_line_prerelease() -> None:
    """
    The differentiator: `line` scope on the SAME fixture as the test above
    only picks up 1.0.0-beta.1 (matching new_version's line), NOT the
    abandoned 0.2.0-beta.1 -- proving `line` and `since_stable` are
    genuinely different selection strategies, not aliases.
    """
    c_abandoned, c_current = _parsed("cA"), _parsed("cB")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [c_abandoned]}, minutes_ago=80),
        V10_BETA1: _release(V10_BETA1, {"feature": [c_current]}, minutes_ago=40),
        V10: _release(V10, {}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V10, scope=SCOPE_LINE)

    assert _shas(result) == {"cB"}


def test_since_stable_scope_stops_at_previous_stable_boundary() -> None:
    """
    `since_stable` must NOT reach past the previous stable tag: a prerelease
    that predates an EARLIER stable release (v0.1.0) must not be folded into
    the CURRENT finalize (v0.2.0), even though it's still in `released`.
    """
    c_old_line, c_current = _parsed("cOLD"), _parsed("cNEW")
    v0_0_1_beta1 = Version.parse("0.0.1-beta.1")
    released = {
        v0_0_1_beta1: _release(
            v0_0_1_beta1, {"feature": [c_old_line]}, minutes_ago=200
        ),
        V01: _release(V01, {}, minutes_ago=100),  # the boundary: previous stable
        V02_BETA1: _release(V02_BETA1, {"feature": [c_current]}, minutes_ago=30),
        V02: _release(V02, {}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02, scope=SCOPE_SINCE_STABLE)

    assert _shas(result) == {"cNEW"}
    assert "cOLD" not in _shas(result)


def test_dedup_by_sha_across_prerelease_and_own_release() -> None:
    """
    A commit sha appearing in BOTH a prerelease's elements and the stable
    finalize's own elements (an edge case, not expected under normal git
    topology but explicitly specified) is listed exactly once.
    """
    shared = _parsed("shared-sha")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [shared]}, minutes_ago=30),
        V02: _release(V02, {"feature": [shared]}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02, scope=SCOPE_LINE)

    assert len(result["elements"]["feature"]) == 1
    assert _shas(result) == {"shared-sha"}


def test_dedup_across_two_prereleases_with_different_commit_types() -> None:
    """A sha reused across two DIFFERENT type buckets is still merged once."""
    dup = _parsed("dup-sha", commit_type="feature")
    dup_as_fix = _parsed("dup-sha", commit_type="fix")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [dup]}, minutes_ago=60),
        V02_BETA2: _release(V02_BETA2, {"fix": [dup_as_fix]}, minutes_ago=30),
        V02: _release(V02, {}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02, scope=SCOPE_LINE)

    total = sum(len(commits) for commits in result["elements"].values())
    assert total == 1
    assert _shas(result, "feature") == {"dup-sha"}
    assert _shas(result, "fix") == set()  # the later (fix) copy lost the race


def test_no_intervening_prereleases_returns_own_release_unchanged() -> None:
    """
    Empty/edge case: a stable release with NO intervening prereleases at all
    is unaffected by aggregation (the "line" scope loop finds nothing).
    """
    c1 = _parsed("c1")
    released = {
        V01: _release(V01, {}, minutes_ago=50),
        V02: _release(V02, {"feature": [c1]}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02, scope=SCOPE_LINE)

    assert result == rh.released[V02]
    assert _shas(result) == {"c1"}


def test_prerelease_new_version_finds_nothing_to_merge() -> None:
    """
    Calling this for a PREreleasing `new_version` (not the intended usage,
    the CLI seam guards against it) simply finds no candidate prereleases to
    merge, since selection only ever looks for OTHER prereleases relative to
    `new_version`'s own (major, minor, patch) line/boundary.
    """
    c1 = _parsed("c1")
    released = {
        V01: _release(V01, {}, minutes_ago=50),
        V02_BETA1: _release(V02_BETA1, {"feature": [c1]}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02_BETA1, scope=SCOPE_LINE)

    assert result == rh.released[V02_BETA1]


def test_multiple_prerelease_tokens_in_same_line_all_aggregate() -> None:
    """
    A line cut with two DIFFERENT prerelease tokens (e.g. alpha then beta --
    a project changing its mind about naming mid-line) still aggregates
    both: `line` scope only checks (major, minor, patch), not the token.
    """
    v_alpha = Version.parse("0.2.0-alpha.1")
    v_beta = Version.parse("0.2.0-beta.1")
    c_alpha, c_beta = _parsed("cAlpha"), _parsed("cBeta")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        v_alpha: _release(v_alpha, {"feature": [c_alpha]}, minutes_ago=60),
        v_beta: _release(v_beta, {"feature": [c_beta]}, minutes_ago=30),
        V02: _release(V02, {}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02, scope=SCOPE_LINE)

    assert _shas(result) == {"cAlpha", "cBeta"}


def test_default_scope_parameter_is_line() -> None:
    c1 = _parsed("c1")
    released = {
        V01: _release(V01, {}, minutes_ago=100),
        V02_BETA1: _release(V02_BETA1, {"feature": [c1]}, minutes_ago=30),
        V02: _release(V02, {}, minutes_ago=0),
    }
    rh = _history(released)

    result = aggregate_stable_release(rh, new_version=V02)  # no scope kwarg

    assert _shas(result) == {"c1"}
