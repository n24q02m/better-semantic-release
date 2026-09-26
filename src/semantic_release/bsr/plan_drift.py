"""
Plan-snapshot drift checking (`bsr.plan_drift`, W1.4).

`plan --write` produces a schema-v1 snapshot of the release decision. Before
that snapshot is consumed — `verify --plan` (pre-release gate) or
`publish --plan` (opt-in publish gate) — the consumer MUST fail closed unless
the current repository state still matches what was planned (spec §4.1C).

Drift definition (deliberately narrow, wave-1 plan §4):
- ``head_sha``   — the repository moved since the plan was made (verify only;
  publish anchors on the tag instead because the `version` step legitimately
  creates a release commit after planning),
- ``next_version`` — the recomputed release state no longer agrees,
- ``tag existence`` — for verify the planned tag must not exist yet; for
  publish the published tag must exist and be reachable from HEAD.

Everything here is pure: file loading raises :class:`PlanSnapshotError`, the
drift evaluators return findings; the CLI commands decide how to render them
(CheckResult rows for `verify`, stderr + exit 1 for `publish`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from semantic_release.bsr.plan import SCHEMA_VERSION, ReleasePlan


class PlanSnapshotError(Exception):
    """A plan snapshot could not be loaded or is not a schema we implement."""


@dataclass(frozen=True)
class DriftFinding:
    """One mismatch between a plan snapshot and the current repository state."""

    code: str
    message: str
    remediation: str


def load_plan_snapshot(path: Path | str) -> ReleasePlan:
    """
    Load a ``plan --write`` artifact, strictly.

    Fails closed on: unreadable/missing file, malformed JSON, a
    ``schema_version`` we do not implement, or a snapshot without
    ``head_sha`` (a plan that cannot answer "did the repo move?" is not
    consumable).
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        msg = f"cannot read plan snapshot: {exc}"
        raise PlanSnapshotError(msg) from exc
    except json.JSONDecodeError as exc:
        msg = f"plan snapshot is not valid JSON: {exc}"
        raise PlanSnapshotError(msg) from exc

    if not isinstance(raw, dict):
        raise PlanSnapshotError("plan snapshot must be a JSON object")

    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise PlanSnapshotError(
            f"unsupported plan schema_version {schema_version!r} "
            f"(this bsr implements {SCHEMA_VERSION}); refusing to consume"
        )

    if raw.get("head_sha") is None:
        raise PlanSnapshotError(
            "plan snapshot has no head_sha; re-run `plan --write` to produce "
            "a consumable snapshot"
        )

    return ReleasePlan.model_validate(raw)


def verify_plan_drift(
    snapshot: ReleasePlan,
    *,
    head_sha: str,
    current_version: str,
    planned_tag_exists: bool,
) -> tuple[DriftFinding, ...]:
    """
    Pre-release drift check for ``verify --plan``.

    The planned tag must NOT exist yet: its existence means the release was
    already cut and the snapshot is consumed.
    """
    findings: list[DriftFinding] = []
    if snapshot.head_sha != head_sha:
        findings.append(
            DriftFinding(
                code="PLAN_HEAD_DRIFT",
                message=(
                    f"plan targets HEAD {snapshot.head_sha}, repository is at {head_sha}"
                ),
                remediation="re-run `plan --write` and re-review the new snapshot",
            )
        )
    if snapshot.version != current_version:
        findings.append(
            DriftFinding(
                code="PLAN_VERSION_DRIFT",
                message=(
                    f"plan computed version {snapshot.version!r}, repository "
                    f"now computes {current_version!r}"
                ),
                remediation="re-run `plan --write` and re-review the new snapshot",
            )
        )
    if planned_tag_exists and snapshot.tag is not None:
        findings.append(
            DriftFinding(
                code="PLAN_TAG_EXISTS",
                message=(
                    f"planned tag {snapshot.tag!r} already exists; the snapshot "
                    "is consumed"
                ),
                remediation="re-run `plan --write` for the next release",
            )
        )
    return tuple(findings)


def publish_plan_drift(
    snapshot: ReleasePlan,
    *,
    publish_tag: str,
    current_version: str,
    planned_tag_reachable: bool | None,
) -> tuple[DriftFinding, ...]:
    """
    Publish-time drift check for ``publish --plan``.

    Anchors on the tag rather than ``head_sha``: between planning and
    publishing the `version` step legitimately creates a release commit (and
    the tag), so HEAD equality would false-positive on the standard flow. The
    safety property for publishing is: we publish exactly the planned tag,
    the release state still agrees with the plan, and the tag is reachable
    from HEAD (not orphaned by a rewrite). ``planned_tag_reachable`` is None
    when the tag does not exist at all, which is always drift here.
    """
    findings: list[DriftFinding] = []
    if snapshot.tag != publish_tag:
        findings.append(
            DriftFinding(
                code="PLAN_TAG_MISMATCH",
                message=(
                    f"plan targets tag {snapshot.tag!r}, publish was asked for "
                    f"{publish_tag!r}"
                ),
                remediation=(
                    "publish the planned tag, or re-run `plan --write` and "
                    "re-review the new snapshot"
                ),
            )
        )
    if snapshot.version != current_version:
        findings.append(
            DriftFinding(
                code="PLAN_VERSION_DRIFT",
                message=(
                    f"plan computed version {snapshot.version!r}, repository "
                    f"now computes {current_version!r}"
                ),
                remediation="re-run `plan --write` and re-review the new snapshot",
            )
        )
    if planned_tag_reachable is not True:
        if planned_tag_reachable is None:
            findings.append(
                DriftFinding(
                    code="PLAN_TAG_MISSING",
                    message=(
                        f"planned tag {snapshot.tag!r} does not exist; run "
                        "`version` to cut the release first"
                    ),
                    remediation="run `version` before `publish --plan`",
                )
            )
        else:
            findings.append(
                DriftFinding(
                    code="PLAN_TAG_UNREACHABLE",
                    message=(
                        f"planned tag {snapshot.tag!r} is not reachable from "
                        "HEAD (rewritten history?)"
                    ),
                    remediation="verify tag reachability before publishing",
                )
            )
    return tuple(findings)
