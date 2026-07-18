"""
better-semantic-release additions (bsr): stable-notes aggregation
(`bsr.stable_notes_aggregate`).

A prerelease finalize "consumes" commits: `ReleaseHistory.from_git_history`
(`changelog/release_history.py`) buckets every commit under the NEAREST tag
walking backwards from HEAD, so once rc/beta tags exist for a line, a stable
finalize with no BRAND NEW commits since the last prerelease sees a
genuinely EMPTY `elements` bucket of its own -- the changelog/release-notes
section for the stable tag renders with no commits at all, or a fragmented
view split across the prerelease tags instead of one grouped `vX.Y.Z`
section. This is the most-cited PSR changelog complaint, "won't fix"
upstream -- issues #555, #817, #1440, #1377.

This module merges the `elements` of every intervening prerelease `Release`
into the stable release's own `elements`, de-duplicated by commit sha, and
returns a NEW `Release` for the caller to substitute into
`release_history.released[new_version]` -- narrow, opt-in, off by default,
because it changes the changelog CONTENT (a committed artifact).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from semantic_release.changelog.release_history import Release

if TYPE_CHECKING:
    from semantic_release.changelog.release_history import ReleaseHistory
    from semantic_release.commit_parser import ParseResult
    from semantic_release.version.version import Version

SCOPE_LINE = "line"
SCOPE_SINCE_STABLE = "since_stable"


def _same_line(a: Version, b: Version) -> bool:
    return a.major == b.major and a.minor == b.minor and a.patch == b.patch


def _chronological_others(
    release_history: ReleaseHistory, new_version: Version
) -> list[Version]:
    """Every OTHER released version, most-recently-tagged first."""
    others = [version for version in release_history.released if version != new_version]
    others.sort(key=lambda v: release_history.released[v]["tagged_date"], reverse=True)
    return others


def _select_intervening_prereleases(
    release_history: ReleaseHistory, new_version: Version, scope: str
) -> list[Version]:
    """
    Select the prerelease Versions to fold into `new_version`'s stable
    notes, OLDEST first.

    `scope="line"` (default): every prerelease sharing `new_version`'s
    (major, minor, patch) -- a released X.Y.Z can only be finalized once
    (`ReleaseHistory.release` raises on a repeat), so line membership alone
    is an unambiguous match, regardless of chronological position.

    `scope="since_stable"`: walk backwards (most-recently-tagged first) from
    `new_version`, collecting EVERY intervening prerelease regardless of
    line, until the previous STABLE (non-prerelease) tag is reached -- that
    tag is the aggregation boundary and is itself excluded. This differs
    from `"line"` when a prerelease track was abandoned mid-line (e.g. a
    forced bump moved 0.2.0-beta.1 to 1.0.0-beta.1): `"line"` would only
    pick up 1.0.0-beta.1, while `"since_stable"` also folds in the
    abandoned 0.2.0-beta.1.
    """
    others = _chronological_others(release_history, new_version)

    if scope == SCOPE_SINCE_STABLE:
        selected = []
        for version in others:
            if not version.is_prerelease:
                break
            selected.append(version)
    else:
        selected = [
            version
            for version in others
            if version.is_prerelease and _same_line(version, new_version)
        ]

    selected.reverse()  # oldest first, for a naturally-ordered merge
    return selected


def aggregate_stable_release(
    release_history: ReleaseHistory,
    new_version: Version,
    scope: str = SCOPE_LINE,
) -> Release:
    """
    Build the aggregated stable `Release` for `new_version`.

    Merges the `elements` of every intervening prerelease `Release`
    (selected per `scope`) into `new_version`'s own (already-released)
    `elements`, de-duplicated by commit sha so a commit appearing in both a
    prerelease and the stable finalize's own diff is listed once. Returns a
    NEW `Release` TypedDict -- `release_history.released` is read, not
    mutated; the caller substitutes the result back in.

    Callers should only invoke this for a genuine stable finalize (`not
    new_version.is_prerelease`); this only ever selects PREreleases to merge
    in, so calling it for a prerelease `new_version` would just find nothing
    to aggregate. When there is nothing to aggregate (a stable release with
    no intervening prereleases), `new_version`'s own `Release` is returned
    unchanged.
    """
    own_release = release_history.released[new_version]
    intervening = _select_intervening_prereleases(release_history, new_version, scope)
    if not intervening:
        return own_release

    merged: dict[str, list[ParseResult]] = defaultdict(list)
    seen_shas: set[str] = set()

    def _merge_in(elements: dict[str, list[ParseResult]]) -> None:
        for commit_type, parsed_results in elements.items():
            for parsed_result in parsed_results:
                if parsed_result.hexsha in seen_shas:
                    continue
                seen_shas.add(parsed_result.hexsha)
                merged[commit_type].append(parsed_result)

    # Oldest prereleases first, then the finalize's own (newest) commits, so
    # the underlying data is naturally chronological (the changelog template
    # re-sorts each type's commits alphabetically by description anyway, so
    # this ordering is a data-hygiene choice, not a rendering requirement).
    for prerelease_version in intervening:
        _merge_in(release_history.released[prerelease_version]["elements"])
    _merge_in(own_release["elements"])

    return Release(
        tagger=own_release["tagger"],
        committer=own_release["committer"],
        tagged_date=own_release["tagged_date"],
        elements=merged,
        version=own_release["version"],
    )
