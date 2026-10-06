"""
End-to-end drive of the gated release state machine.

The recipe workflow (tests/e2e/gated/recipe.workflow.yml) models the same
flow on GitHub's own Environment gate. This module drives the identical
transitions in-process with an injected adapter so the two cannot drift:
it asserts the environment name matches ``DEFAULT_GATE_ENVIRONMENT`` and
exercises every terminal path (approve -> publish, deny -> abort, timeout
-> abort, idempotent retry) plus the invalid transitions the pipeline must
refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from semantic_release.bsr.gating import (
    DEFAULT_GATE_ENVIRONMENT,
    ApprovalAdapter,
    ApprovalDecision,
    CallbackApprovalAdapter,
    GatePhase,
    GateTransitionError,
    PendingRelease,
    begin_gate,
    finalize,
    record_decision,
    record_timeout,
    request_approval,
)

VERSION = "1.2.3"
HEAD = "a" * 40


def _release() -> PendingRelease:
    return PendingRelease(version=VERSION, head_sha=HEAD)


class _ScriptedAdapter:
    """ApprovalAdapter stub whose decision is fixed by the caller."""

    def __init__(self, outcome: str) -> None:
        self._decision = ApprovalDecision(outcome=outcome)
        self.requested: list[PendingRelease] = []

    def request(self, release: PendingRelease) -> None:
        self.requested.append(release)

    def check(self, release: PendingRelease) -> ApprovalDecision:
        assert release.version == VERSION
        return self._decision


class TestRecipeContract:
    """The recipe workflow and the code must agree on the gate surface."""

    def test_default_environment_is_release_gate(self) -> None:
        assert DEFAULT_GATE_ENVIRONMENT == "release-gate"

    def test_recipe_declares_the_gate_environment(self) -> None:
        recipe = (Path(__file__).parent / "gated" / "recipe.workflow.yml").read_text(
            encoding="utf-8"
        )
        assert f"environment: {DEFAULT_GATE_ENVIRONMENT}" in recipe

    def test_pending_release_defaults_to_the_gate_environment(self) -> None:
        assert _release().environment == DEFAULT_GATE_ENVIRONMENT

    def test_pending_release_validates_inputs(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            PendingRelease(version="   ")
        with pytest.raises(ValueError, match="head_sha"):
            PendingRelease(version=VERSION, head_sha="not-a-sha")


class TestApprovePath:
    def test_full_approve_flow_publishes_once(self) -> None:
        adapter = _ScriptedAdapter("approved")
        state = begin_gate(_release())
        assert state.phase is GatePhase.PENDING
        state = request_approval(state, adapter)
        assert state.phase is GatePhase.AWAITING_APPROVAL
        assert len(adapter.requested) == 1
        state = record_decision(state, ApprovalDecision(outcome="approved", by="alice"))
        assert state.phase is GatePhase.APPROVED
        assert state.decision is not None
        assert state.decision.by == "alice"
        published: list[PendingRelease] = []
        state = finalize(state, publish=published.append)
        assert state.phase is GatePhase.PUBLISHED
        assert state.published is True
        assert [r.version for r in published] == [VERSION]

    def test_idempotent_retry_does_not_publish_twice(self) -> None:
        adapter = _ScriptedAdapter("approved")
        approved = record_decision(
            request_approval(begin_gate(_release()), adapter),
            ApprovalDecision.approved(by="alice"),
        )
        once = finalize(approved, publish=lambda _r: None)
        retried = finalize(once, already_published=True)
        assert retried.phase is GatePhase.PUBLISHED
        assert retried is once  # terminal gates are sticky; a rerun appends nothing

    def test_pending_decision_keeps_gate_awaiting(self) -> None:
        state = request_approval(begin_gate(_release()), _ScriptedAdapter("pending"))
        again = record_decision(state, ApprovalDecision.pending())
        assert again.phase is GatePhase.AWAITING_APPROVAL

    def test_callback_adapter_injection_point(self) -> None:
        adapter = CallbackApprovalAdapter(
            on_check=lambda _r: ApprovalDecision.approved(by="ci"),
            on_request=lambda _r: None,
        )
        state = record_decision(
            request_approval(begin_gate(_release()), adapter),
            ApprovalDecision.approved(by="ci"),
        )
        assert finalize(state, publish=lambda _r: None).phase is GatePhase.PUBLISHED


class TestDenyPath:
    def test_denial_aborts_without_publishing(self) -> None:
        state = request_approval(begin_gate(_release()), _ScriptedAdapter("denied"))
        state = record_decision(
            state, ApprovalDecision(outcome="denied", reason="regression")
        )
        assert state.phase is GatePhase.DENIED
        published: list[PendingRelease] = []
        state = finalize(state, publish=published.append)
        assert state.phase is GatePhase.ABORTED
        assert published == []

    def test_timeout_aborts_without_publishing(self) -> None:
        state = request_approval(begin_gate(_release()), _ScriptedAdapter("pending"))
        state = record_timeout(state)
        assert state.phase is GatePhase.TIMED_OUT
        state = finalize(state, publish=lambda _r: None)
        assert state.phase is GatePhase.ABORTED

    def test_rerun_after_abort_is_sticky(self) -> None:
        state = record_timeout(
            request_approval(begin_gate(_release()), _ScriptedAdapter("pending"))
        )
        aborted = finalize(state)
        assert finalize(aborted) is aborted


class TestInvalidTransitions:
    def test_cannot_finalize_unresolved_gate(self) -> None:
        state = request_approval(begin_gate(_release()), _ScriptedAdapter("pending"))
        with pytest.raises(GateTransitionError, match="awaiting-approval"):
            finalize(state)

    def test_cannot_finalize_pending_gate(self) -> None:
        with pytest.raises(GateTransitionError, match="pending"):
            finalize(begin_gate(_release()))

    def test_decision_requires_awaiting_state(self) -> None:
        with pytest.raises(GateTransitionError, match="pending"):
            record_decision(begin_gate(_release()), ApprovalDecision.approved())

    def test_timeout_requires_awaiting_state(self) -> None:
        with pytest.raises(GateTransitionError, match="pending"):
            record_timeout(begin_gate(_release()))

    def test_cannot_request_approval_from_terminal_phase(self) -> None:
        published = finalize(
            record_decision(
                request_approval(begin_gate(_release()), _ScriptedAdapter("approved")),
                ApprovalDecision.approved(),
            ),
            publish=lambda _r: None,
        )
        with pytest.raises(GateTransitionError, match="published"):
            request_approval(published, _ScriptedAdapter("approved"))

    def test_unknown_outcome_is_rejected(self) -> None:
        with pytest.raises(Exception, match="unknown approval outcome"):
            ApprovalDecision(outcome="maybe")

    def test_adapter_protocol_is_runtime_checkable(self) -> None:
        adapter = _ScriptedAdapter("approved")
        assert isinstance(adapter, ApprovalAdapter)
