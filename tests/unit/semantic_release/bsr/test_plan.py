"""
Golden tests for the ``ReleasePlan`` spine (`bsr.plan`).

Task 1 of the universal-release-engine plan: lock the JSON schema v1 BEFORE
other code starts depending on it, and lock the parity between
``ReleasePlan.to_legacy_document()`` and the historical inline layout that
``jsonout.build_version_document`` used to produce by hand.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from semantic_release.bsr.explain import BumpStats, ReleaseDecision
from semantic_release.bsr.plan import (
    Blocker,
    RegistryStatus,
    build_release_plan,
    is_prerelease,
)
from semantic_release.bsr.summary import ComponentPlan
from semantic_release.enums import LevelBump
from semantic_release.version.version import Version

# ---------------------------------------------------------------------------
# helpers


def _version(major: int = 1, minor: int = 2, patch: int = 3) -> Version:
    return Version(major, minor, patch)


def _bump_stats(level: LevelBump = LevelBump.MINOR, count: int = 7) -> BumpStats:
    return BumpStats(
        level_bump=level,
        commit_count=count,
        latest_version=_version(),
        type_counts={"feat": 2, "fix": 5},
    )


def _component(
    name: str = "api",
    *,
    would_release: bool = True,
    level: str = "MINOR",
    resulting: Version | None = None,
) -> ComponentPlan:
    return ComponentPlan(
        name=name,
        would_release=would_release,
        level=level,
        commit_count=7,
        sample_paths=(f"{name}/x.py",),
        resulting_version=resulting if resulting is not None else _version(1, 3, 0),
    )


def _decision(count: int = 7) -> ReleaseDecision:
    return ReleaseDecision(reason="NO_QUALIFYING_COMMITS", commit_count=count)


# ---------------------------------------------------------------------------
# parity with the historical jsonout layout


def test_legacy_document_parity_full_state() -> None:
    """to_legacy_document() == the exact dict the old jsonout code produced."""
    decision = _decision()
    bump_stats = _bump_stats(LevelBump.MINOR)
    components = [
        _component("api"),
        _component(
            "lib", would_release=False, level="NONE", resulting=_version(1, 2, 3)
        ),
    ]

    plan = build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=decision,
        bump_stats=bump_stats,
        components=components,
    )

    # Literal replication of the historical `build_version_document` body.
    expected: dict[str, Any] = {
        "schema_version": 1,
        "released": False,
        "version": None,
        "tag": None,
        "is_prerelease": False,
        "previous_version": "1.2.3",
        "reason": "NO_QUALIFYING_COMMITS",
        "commit_count": 7,
        "level_bump": "minor",
        "type_counts": {"feat": 2, "fix": 5},
        "components": [
            {
                "name": "api",
                "would_release": True,
                "level": "MINOR",
                "commit_count": 7,
                "sample_paths": ["api/x.py"],
                "resulting_version": str(_version(1, 3, 0)),
            },
            {
                "name": "lib",
                "would_release": False,
                "level": "NONE",
                "commit_count": 7,
                "sample_paths": ["lib/x.py"],
                "resulting_version": str(_version(1, 2, 3)),
            },
        ],
    }
    assert plan.to_legacy_document() == expected


def test_legacy_document_parity_decision_only() -> None:
    """Decision present, no bump stats: legacy fallbacks must hold."""
    plan = build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=_decision(3),
        bump_stats=None,
        components=(),
    )
    doc = plan.to_legacy_document()
    assert doc["reason"] == "NO_QUALIFYING_COMMITS"
    assert doc["commit_count"] == 3
    assert doc["level_bump"] is None
    assert doc["type_counts"] == {}


def test_legacy_document_parity_bump_only() -> None:
    """Bump stats present, no decision (forced-level path): commit_count falls back."""
    plan = build_release_plan(
        released=True,
        version="1.3.0",
        tag="v1.3.0",
        previous_version="1.2.3",
        decision=None,
        bump_stats=_bump_stats(LevelBump.MINOR),
        components=(),
    )
    doc = plan.to_legacy_document()
    assert doc["reason"] is None
    assert doc["commit_count"] == 7
    assert doc["level_bump"] == "minor"


# ---------------------------------------------------------------------------
# structured schema v1 golden documents


def test_schema_release() -> None:
    plan = build_release_plan(
        released=True,
        version="1.3.0",
        tag="v1.3.0",
        previous_version="1.2.3",
        decision=None,
        bump_stats=_bump_stats(LevelBump.MINOR),
        components=(),
    )
    doc = json.loads(json.dumps(plan.to_document()))  # JSON-clean
    assert doc == {
        "schema_version": 1,
        "released": True,
        "version": "1.3.0",
        "tag": "v1.3.0",
        "is_prerelease": False,
        "previous_version": "1.2.3",
        "decision": None,
        "bump": {
            "level_bump": "minor",
            "commit_count": 7,
            "type_counts": {"feat": 2, "fix": 5},
        },
        "components": [],
        "blockers": [],
        "registry": None,
        "publish_target": None,
    }


def test_schema_no_release() -> None:
    plan = build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=ReleaseDecision(reason="ALREADY_RELEASED_NOOP", commit_count=0),
        bump_stats=None,
        components=(),
    )
    doc = plan.to_document()
    assert doc["released"] is False
    assert doc["decision"] == {"code": "ALREADY_RELEASED_NOOP", "commit_count": 0}
    assert doc["bump"] == {"level_bump": None, "commit_count": 0, "type_counts": {}}


def test_schema_blocked_release() -> None:
    plan = build_release_plan(
        released=False,
        version="1.3.0",
        tag="v1.3.0",
        previous_version="1.2.3",
        decision=None,
        bump_stats=_bump_stats(LevelBump.MINOR),
        components=(),
        blockers=(
            Blocker(
                code="REGISTRY_COLLISION",
                message="1.3.0 already exists on pypi",
                remediation="bump the version or resolve the collision",
            ),
        ),
        registry=RegistryStatus(
            registry="pypi", reachable=True, detail="collision found"
        ),
    )
    assert plan.is_blocked is True
    doc = plan.to_document()
    assert doc["blockers"] == [
        {
            "code": "REGISTRY_COLLISION",
            "message": "1.3.0 already exists on pypi",
            "remediation": "bump the version or resolve the collision",
        }
    ]
    assert doc["registry"] == {
        "registry": "pypi",
        "reachable": True,
        "detail": "collision found",
    }


def test_schema_monorepo_two_components() -> None:
    plan = build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=None,
        bump_stats=None,
        components=(
            _component(
                "api", would_release=True, level="MINOR", resulting=_version(1, 3, 0)
            ),
            _component(
                "lib", would_release=False, level="NONE", resulting=_version(1, 2, 3)
            ),
        ),
    )
    doc = plan.to_document()
    assert [c["name"] for c in doc["components"]] == ["api", "lib"]
    assert [c["would_release"] for c in doc["components"]] == [True, False]
    assert doc["components"][0]["resulting_version"] == "1.3.0"
    assert doc["components"][1]["resulting_version"] == "1.2.3"


@pytest.mark.parametrize(
    "version, expected",
    [
        ("1.2.3", False),
        ("1.2.3-rc.1", True),
        ("1.2.3+build.5", False),
        ("1.2.3-rc.1+build.5", True),
        (None, False),
    ],
)
def test_is_prerelease(version: str | None, expected: bool) -> None:
    assert is_prerelease(version) is expected
