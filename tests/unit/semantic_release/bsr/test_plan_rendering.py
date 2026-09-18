"""Rendering golden tests for ``ReleasePlan`` (table + markdown)."""

from __future__ import annotations

from semantic_release.bsr.plan import (
    Blocker,
    build_release_plan,
)

from tests.unit.semantic_release.bsr.test_plan import _bump_stats, _component, _decision


def _plan_with_components() -> object:
    return build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=_decision(3),
        bump_stats=None,
        components=(
            _component("api", would_release=True, level="MINOR"),
            _component("lib", would_release=False, level="NONE"),
        ),
        blockers=(
            Blocker(
                code="ORPHAN_TAG",
                message="tag v1.2.3 is orphaned",
                remediation="re-tag",
            ),
        ),
    )


def test_table_contains_components_and_blockers() -> None:
    plan = _plan_with_components()
    table = plan.render_table()  # type: ignore[attr-defined]
    assert "better-semantic-release release plan" in table
    assert "released:         no" in table
    assert "ORPHAN_TAG" in table
    assert "re-tag" in table
    assert "api" in table
    assert "lib" in table


def test_markdown_contains_summary_and_rows() -> None:
    plan = _plan_with_components()
    md = plan.render_markdown()  # type: ignore[attr-defined]
    assert "## Release plan" in md
    assert "| released | no |" in md
    assert "| component | would release | level | commits | resulting version |" in md
    assert "| api | yes | MINOR | 7 |" in md
    assert "### Blockers" in md


# reuse the shared bump-stats helper so both modules construct identically
_ = _bump_stats
