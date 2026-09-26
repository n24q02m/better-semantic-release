"""
Unit tests for plan-snapshot drift checking (W1.4).

`load_plan_snapshot` must fail closed on anything that is not a consumable
schema-v1 snapshot; the drift evaluators must report exactly the narrow drift
definition (head anchor, computed version, tag state) — no more, no less.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from semantic_release.bsr.plan_drift import (
    PlanSnapshotError,
    load_plan_snapshot,
    publish_plan_drift,
    verify_plan_drift,
)

_HEAD = "1d3e5a7b" * 5


def _valid_snapshot() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "released": True,
        "version": "0.2.0",
        "tag": "v0.2.0",
        "is_prerelease": False,
        "previous_version": "0.1.0",
        "head_sha": _HEAD,
        "decision": None,
        "bump": {"level_bump": "minor", "commit_count": 1, "type_counts": {}},
        "components": [],
        "blockers": [],
        "registry": None,
        "publish_target": None,
    }


# ---------------------------------------------------------------------------
# load_plan_snapshot


def test_load_plan_snapshot_round_trips(tmp_path: Any) -> None:
    path = tmp_path / "release-plan.json"
    path.write_text(json.dumps(_valid_snapshot()), encoding="utf-8")
    snapshot = load_plan_snapshot(path)
    assert snapshot.schema_version == 1
    assert snapshot.head_sha == _HEAD
    assert snapshot.version == "0.2.0"
    assert snapshot.tag == "v0.2.0"


def test_load_plan_snapshot_missing_file(tmp_path: Any) -> None:
    with pytest.raises(PlanSnapshotError, match="cannot read plan snapshot"):
        load_plan_snapshot(tmp_path / "nope.json")


def test_load_plan_snapshot_invalid_json(tmp_path: Any) -> None:
    path = tmp_path / "release-plan.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PlanSnapshotError, match="not valid JSON"):
        load_plan_snapshot(path)


def test_load_plan_snapshot_rejects_non_object(tmp_path: Any) -> None:
    path = tmp_path / "release-plan.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(PlanSnapshotError, match="must be a JSON object"):
        load_plan_snapshot(path)


@pytest.mark.parametrize("schema_version", [0, 2, "1", None])
def test_load_plan_snapshot_rejects_unknown_schema(
    tmp_path: Any, schema_version: Any
) -> None:
    snapshot = _valid_snapshot()
    snapshot["schema_version"] = schema_version
    path = tmp_path / "release-plan.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(PlanSnapshotError, match="unsupported plan schema_version"):
        load_plan_snapshot(path)


def test_load_plan_snapshot_rejects_missing_head_sha(tmp_path: Any) -> None:
    snapshot = _valid_snapshot()
    del snapshot["head_sha"]
    path = tmp_path / "release-plan.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(PlanSnapshotError, match="no head_sha"):
        load_plan_snapshot(path)


# ---------------------------------------------------------------------------
# verify_plan_drift (pre-release: planned tag must NOT exist)


def _snapshot() -> Any:
    from semantic_release.bsr.plan import ReleasePlan

    return ReleasePlan.model_validate(_valid_snapshot())


def test_verify_drift_clean_snapshot_matches() -> None:
    findings = verify_plan_drift(
        _snapshot(),
        head_sha=_HEAD,
        current_version="0.2.0",
        planned_tag_exists=False,
    )
    assert findings == ()


def test_verify_drift_head_moved() -> None:
    findings = verify_plan_drift(
        _snapshot(),
        head_sha="f" * 40,
        current_version="0.2.0",
        planned_tag_exists=False,
    )
    assert [f.code for f in findings] == ["PLAN_HEAD_DRIFT"]


def test_verify_drift_version_changed() -> None:
    findings = verify_plan_drift(
        _snapshot(),
        head_sha=_HEAD,
        current_version="0.3.0",
        planned_tag_exists=False,
    )
    assert [f.code for f in findings] == ["PLAN_VERSION_DRIFT"]


def test_verify_drift_planned_tag_already_exists() -> None:
    findings = verify_plan_drift(
        _snapshot(),
        head_sha=_HEAD,
        current_version="0.2.0",
        planned_tag_exists=True,
    )
    assert [f.code for f in findings] == ["PLAN_TAG_EXISTS"]


def test_verify_drift_aggregates_all_mismatches() -> None:
    findings = verify_plan_drift(
        _snapshot(),
        head_sha="f" * 40,
        current_version="0.3.0",
        planned_tag_exists=True,
    )
    assert [f.code for f in findings] == [
        "PLAN_HEAD_DRIFT",
        "PLAN_VERSION_DRIFT",
        "PLAN_TAG_EXISTS",
    ]


def test_verify_drift_tag_check_skipped_for_null_tag() -> None:
    snapshot = _valid_snapshot()
    snapshot["tag"] = None
    from semantic_release.bsr.plan import ReleasePlan

    findings = verify_plan_drift(
        ReleasePlan.model_validate(snapshot),
        head_sha=_HEAD,
        current_version="0.2.0",
        planned_tag_exists=True,
    )
    assert findings == ()


# ---------------------------------------------------------------------------
# publish_plan_drift (publish-time: tag must match, exist, and be reachable)


def test_publish_drift_clean() -> None:
    findings = publish_plan_drift(
        _snapshot(),
        publish_tag="v0.2.0",
        current_version="0.2.0",
        planned_tag_reachable=True,
    )
    assert findings == ()


def test_publish_drift_tag_mismatch() -> None:
    findings = publish_plan_drift(
        _snapshot(),
        publish_tag="v0.1.0",
        current_version="0.2.0",
        planned_tag_reachable=True,
    )
    assert [f.code for f in findings] == ["PLAN_TAG_MISMATCH"]


def test_publish_drift_version_changed() -> None:
    findings = publish_plan_drift(
        _snapshot(),
        publish_tag="v0.2.0",
        current_version="0.3.0",
        planned_tag_reachable=True,
    )
    assert [f.code for f in findings] == ["PLAN_VERSION_DRIFT"]


def test_publish_drift_tag_missing() -> None:
    findings = publish_plan_drift(
        _snapshot(),
        publish_tag="v0.2.0",
        current_version="0.2.0",
        planned_tag_reachable=None,
    )
    assert [f.code for f in findings] == ["PLAN_TAG_MISSING"]


def test_publish_drift_tag_unreachable() -> None:
    findings = publish_plan_drift(
        _snapshot(),
        publish_tag="v0.2.0",
        current_version="0.2.0",
        planned_tag_reachable=False,
    )
    assert [f.code for f in findings] == ["PLAN_TAG_UNREACHABLE"]
