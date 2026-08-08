"""
Unit tests for `bsr.jsonout` (the document builder in isolation).

The CLI seam that actually emits the document lives in
`cli/commands/version.py`; see test_jsonout_cli.py for that.
"""

from __future__ import annotations

import json

from semantic_release.bsr import jsonout
from semantic_release.bsr.explain import NO_QUALIFYING_COMMITS, ReleaseDecision


def test_build_version_document_for_no_release() -> None:
    doc = jsonout.build_version_document(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=ReleaseDecision(reason=NO_QUALIFYING_COMMITS, commit_count=7),
        bump_stats=None,
        components=(),
    )

    assert doc["schema_version"] == 1
    assert doc["released"] is False
    assert doc["version"] is None
    assert doc["reason"] == "NO_QUALIFYING_COMMITS"
    assert doc["commit_count"] == 7
    # Must round-trip: this is the whole point of the format.
    assert json.loads(json.dumps(doc)) == doc


def test_build_version_document_for_a_real_release() -> None:
    doc = jsonout.build_version_document(
        released=True,
        version="1.3.0",
        tag="v1.3.0",
        previous_version="1.2.3",
        decision=None,
        bump_stats=None,
        components=(),
    )

    assert doc["released"] is True
    assert doc["version"] == "1.3.0"
    assert doc["reason"] is None
    assert doc["is_prerelease"] is False


def test_prerelease_is_read_from_the_prerelease_field_not_build_metadata() -> None:
    """
    `1.0.0+build-7` is NOT a prerelease -- the hyphen is inside build metadata.

    SemVer puts the prerelease after `-` and build metadata after `+`, so the
    hyphen only means "prerelease" in the part before the `+`.
    """
    prerelease = jsonout.build_version_document(
        released=True,
        version="1.3.0-rc.1",
        tag="v1.3.0-rc.1",
        previous_version="1.2.3",
        decision=None,
        bump_stats=None,
        components=(),
    )
    build_metadata_only = jsonout.build_version_document(
        released=True,
        version="1.3.0+build-7",
        tag="v1.3.0+build-7",
        previous_version="1.2.3",
        decision=None,
        bump_stats=None,
        components=(),
    )

    assert prerelease["is_prerelease"] is True
    assert build_metadata_only["is_prerelease"] is False


def test_bump_stats_supply_commit_count_and_type_counts_when_released() -> None:
    from semantic_release.bsr.explain import BumpStats
    from semantic_release.enums import LevelBump
    from semantic_release.version.version import Version

    doc = jsonout.build_version_document(
        released=True,
        version="1.3.0",
        tag="v1.3.0",
        previous_version="1.2.3",
        decision=None,
        bump_stats=BumpStats(
            level_bump=LevelBump.MINOR,
            commit_count=4,
            latest_version=Version.parse("1.2.3"),
            type_counts={"feat": 1, "fix": 3},
        ),
        components=(),
    )

    assert doc["commit_count"] == 4
    assert doc["level_bump"] == "minor"
    assert doc["type_counts"] == {"feat": 1, "fix": 3}
    assert json.loads(json.dumps(doc)) == doc


def test_components_mirror_component_plan() -> None:
    from semantic_release.bsr.summary import ComponentPlan
    from semantic_release.version.version import Version

    doc = jsonout.build_version_document(
        released=True,
        version="0.2.0",
        tag="v0.2.0",
        previous_version="0.1.0",
        decision=None,
        bump_stats=None,
        components=(
            ComponentPlan(
                name="web",
                would_release=True,
                level="MINOR",
                commit_count=1,
                sample_paths=("apps/web/y.py",),
                resulting_version=Version.parse("0.2.0"),
            ),
        ),
    )

    assert doc["components"] == [
        {
            "name": "web",
            "would_release": True,
            "level": "MINOR",
            "commit_count": 1,
            "sample_paths": ["apps/web/y.py"],
            "resulting_version": "0.2.0",
        }
    ]
    # A Version object would not survive json.dumps; this proves it was cast.
    assert json.loads(json.dumps(doc)) == doc
