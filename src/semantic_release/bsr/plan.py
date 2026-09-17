"""
better-semantic-release additions (bsr): `ReleasePlan` (bsr.plan).

The structured spine of every release decision. ``summary``, ``explain`` and
``jsonout`` previously each carried their own projection of the same decision;
this module unifies them around one model so the CLI surfaces (``plan``,
``verify``, ``doctor``, structured ``publish``) all read from a single object.

Compatibility contract (see README "Design principles"): the legacy
``version``/``publish`` JSON documents are NOT re-shaped here.
``ReleasePlan.to_legacy_document()`` reproduces the exact historical
``jsonout.build_version_document`` layout, and ``jsonout`` delegates to it --
the parity unit test (``tests/unit/semantic_release/bsr/test_plan.py``) locks
that equivalence so the spine can evolve without breaking legacy consumers.
The NEW structured layout lives in :meth:`ReleasePlan.to_document`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from pydantic import BaseModel, ConfigDict

# Single source of truth for the JSON document schema. `bsr.jsonout` re-exports
# this so existing consumers of `jsonout.SCHEMA_VERSION` keep working; the
# import direction (jsonout -> plan) must stay one-way.
SCHEMA_VERSION = 1

if TYPE_CHECKING:
    from typing import Any, Sequence

    from semantic_release.bsr.explain import BumpStats, ReleaseDecision
    from semantic_release.bsr.summary import ComponentPlan

__all__ = [
    "SCHEMA_VERSION",
    "Blocker",
    "BumpSummary",
    "ComponentPlanRow",
    "DecisionReason",
    "PublishTarget",
    "RegistryStatus",
    "ReleasePlan",
    "build_release_plan",
    "is_prerelease",
]


def is_prerelease(version: str | None) -> bool:
    """A version string counts as a prerelease when it carries a pre-release segment."""
    if version is None:
        return False
    return "-" in version.split("+", 1)[0]


class DecisionReason(BaseModel):
    """Why a release did (or did not) happen, classified by ``bsr.explain``."""

    model_config = ConfigDict(frozen=True)

    code: str
    """One of ``NO_QUALIFYING_COMMITS`` | ``ALREADY_RELEASED_NOOP`` | ``ORPHAN``."""

    commit_count: int


class BumpSummary(BaseModel):
    """Aggregated commit-scan statistics behind the level decision."""

    model_config = ConfigDict(frozen=True)

    level_bump: str | None
    commit_count: int
    type_counts: Mapping[str, int]


class ComponentPlanRow(BaseModel):
    """One component row of the plan (mirrors ``bsr.summary.ComponentPlan``)."""

    model_config = ConfigDict(frozen=True)

    name: str
    would_release: bool
    level: str
    commit_count: int
    sample_paths: tuple[str, ...] = ()
    resulting_version: str


class Blocker(BaseModel):
    """A condition that prevents the release from proceeding."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    remediation: str


class RegistryStatus(BaseModel):
    """Outcome of a (read-only) registry probe, when one was performed."""

    model_config = ConfigDict(frozen=True)

    registry: str
    reachable: bool | None = None
    detail: str | None = None


class PublishTarget(BaseModel):
    """Where the release would be published."""

    model_config = ConfigDict(frozen=True)

    kind: str
    detail: str | None = None


class ReleasePlan(BaseModel):
    """The complete, renderable release decision for one run."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = SCHEMA_VERSION
    released: bool
    version: str | None = None
    tag: str | None = None
    previous_version: str | None = None
    decision: DecisionReason | None = None
    bump: BumpSummary | None = None
    components: tuple[ComponentPlanRow, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    registry: RegistryStatus | None = None
    publish_target: PublishTarget | None = None

    @property
    def is_prerelease(self) -> bool:
        return is_prerelease(self.version)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    def to_document(self) -> dict[str, Any]:
        """Render the structured schema-v1 document (new, opt-in consumers)."""
        doc: dict[str, Any] = {
            "schema_version": self.schema_version,
            "released": self.released,
            "version": self.version,
            "tag": self.tag,
            "is_prerelease": self.is_prerelease,
            "previous_version": self.previous_version,
            "decision": (
                {"code": self.decision.code, "commit_count": self.decision.commit_count}
                if self.decision is not None
                else None
            ),
            "bump": (
                {
                    "level_bump": self.bump.level_bump,
                    "commit_count": self.bump.commit_count,
                    "type_counts": dict(self.bump.type_counts),
                }
                if self.bump is not None
                else None
            ),
            "components": [
                {
                    "name": c.name,
                    "would_release": c.would_release,
                    "level": c.level,
                    "commit_count": c.commit_count,
                    "sample_paths": list(c.sample_paths),
                    "resulting_version": c.resulting_version,
                }
                for c in self.components
            ],
            "blockers": [
                {"code": b.code, "message": b.message, "remediation": b.remediation}
                for b in self.blockers
            ],
            "registry": (
                None
                if self.registry is None
                else {
                    "registry": self.registry.registry,
                    "reachable": self.registry.reachable,
                    "detail": self.registry.detail,
                }
            ),
            "publish_target": (
                None
                if self.publish_target is None
                else {"kind": self.publish_target.kind, "detail": self.publish_target.detail}
            ),
        }
        return doc

    def to_legacy_document(self) -> dict[str, Any]:
        """
        Render the exact historical ``version`` JSON layout (drop-in contract).

        Field-for-field identical to the original
        ``jsonout.build_version_document``; the parity test locks this.
        """
        decision = self.decision
        bump = self.bump
        return {
            "schema_version": SCHEMA_VERSION,
            "released": self.released,
            "version": self.version,
            "tag": self.tag,
            "is_prerelease": self.is_prerelease,
            "previous_version": self.previous_version,
            "reason": decision.code if decision is not None else None,
            "commit_count": (
                decision.commit_count
                if decision is not None
                else (bump.commit_count if bump is not None else 0)
            ),
            "level_bump": bump.level_bump if bump is not None else None,
            "type_counts": dict(bump.type_counts) if bump is not None else {},
            "components": [
                {
                    "name": c.name,
                    "would_release": c.would_release,
                    "level": c.level,
                    "commit_count": c.commit_count,
                    "sample_paths": list(c.sample_paths),
                    "resulting_version": c.resulting_version,
                }
                for c in self.components
            ],
        }

    def render_table(self) -> str:
        """Human-readable table for stderr/CLI (``--format table``)."""
        lines = [
            "better-semantic-release release plan",
            f"  released:         {'yes' if self.released else 'no'}",
            f"  version:          {self.version or '-'}",
            f"  tag:              {self.tag or '-'}",
            f"  previous version: {self.previous_version or '-'}",
            f"  reason:           {self.decision.code if self.decision else '-'}",
        ]
        if self.bump is not None:
            lines.append(f"  level bump:       {self.bump.level_bump or '-'}")
            lines.append(f"  commits:          {self.bump.commit_count}")
        if self.blockers:
            lines.append("  blockers:")
            lines.extend(f"    - [{b.code}] {b.message} (fix: {b.remediation})" for b in self.blockers)
        if self.components:
            lines.append("")
            lines.extend(_component_table(self.components).splitlines())
        return "\n".join(lines)

    def render_markdown(self) -> str:
        """Markdown rendering for ``$GITHUB_STEP_SUMMARY`` (``--format markdown``)."""
        lines = [
            "## Release plan",
            "",
            "| field | value |",
            "| --- | --- |",
            f"| released | {'yes' if self.released else 'no'} |",
            f"| version | `{self.version or '-'}` |",
            f"| tag | `{self.tag or '-'}` |",
            f"| previous version | `{self.previous_version or '-'}` |",
            f"| reason | {self.decision.code if self.decision else '-'} |",
        ]
        if self.components:
            lines.append("")
            lines.append("| component | would release | level | commits | resulting version |")
            lines.append("| --- | --- | --- | --- | --- |")
            lines.extend(
                f"| {c.name} | {'yes' if c.would_release else 'no'} | {c.level} "
                f"| {c.commit_count} | `{c.resulting_version}` |"
                for c in self.components
            )
        if self.blockers:
            lines.append("")
            lines.append("### Blockers")
            lines.append("")
            lines.extend(f"- **{b.code}**: {b.message} — *fix:* {b.remediation}" for b in self.blockers)
        return "\n".join(lines)


def _component_table(components: Sequence[ComponentPlanRow]) -> str:
    headers = ("component", "would-release", "level", "commits", "sample paths", "version")
    rows = [
        (
            c.name,
            "yes" if c.would_release else "no",
            c.level,
            str(c.commit_count),
            ", ".join(c.sample_paths) or "-",
            c.resulting_version,
        )
        for c in components
    ]
    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(headers)
    ]

    def _fmt(row: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(row, widths))

    lines = [
        _fmt(headers),
        _fmt(tuple("-" * width for width in widths)),
        *(_fmt(row) for row in rows),
    ]
    return "\n".join(lines)


def build_release_plan(
    *,
    released: bool,
    version: str | None,
    tag: str | None,
    previous_version: str | None,
    decision: ReleaseDecision | None,
    bump_stats: BumpStats | None,
    components: Sequence[ComponentPlan] = (),
    blockers: Sequence[Blocker] = (),
    registry: RegistryStatus | None = None,
    publish_target: PublishTarget | None = None,
) -> ReleasePlan:
    """
    Build a :class:`ReleasePlan` from the existing decision primitives.

    Accepts the current ``bsr.explain`` / ``bsr.summary`` dataclasses so every
    existing producer can participate without changing its own contract.
    """
    if bump_stats is not None:
        normalized_bump = BumpSummary(
            level_bump=bump_stats.level_bump.name.lower(),
            commit_count=(
                decision.commit_count if decision is not None else bump_stats.commit_count
            ),
            type_counts=dict(bump_stats.type_counts),
        )
    elif decision is not None:
        normalized_bump = BumpSummary(
            level_bump=None,
            commit_count=decision.commit_count,
            type_counts={},
        )
    else:
        normalized_bump = None
    return ReleasePlan(
        released=released,
        version=version,
        tag=tag,
        previous_version=previous_version,
        decision=(
            DecisionReason(code=decision.reason, commit_count=decision.commit_count)
            if decision is not None
            else None
        ),
        bump=normalized_bump,
        components=tuple(
            ComponentPlanRow(
                name=c.name,
                would_release=c.would_release,
                level=c.level,
                commit_count=c.commit_count,
                sample_paths=tuple(c.sample_paths),
                resulting_version=str(c.resulting_version),
            )
            for c in components
        ),
        blockers=tuple(blockers),
        registry=registry,
        publish_target=publish_target,
    )
