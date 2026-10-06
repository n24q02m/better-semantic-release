"""Unit tests for the gated-release state machine (wave 2, W2.1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from semantic_release.bsr.gating import GateState

from semantic_release.bsr.gating import (
    DEFAULT_GATE_ENVIRONMENT,
    RELEASE_MODE_GATED,
    ApprovalDecision,
    CallbackApprovalAdapter,
    FileApprovalAdapter,
    GateDecisionError,
    GatePhase,
    GateTransitionError,
    PendingRelease,
    begin_gate,
    finalize,
    record_decision,
    record_timeout,
    request_approval,
    run_gated_release,
)


def _release(version: str = "1.4.0") -> PendingRelease:
    return PendingRelease(
        version=version,
        environment=DEFAULT_GATE_ENVIRONMENT,
        head_sha="0123456789abcdef0123456789abcdef01234567",
    )


def _awaiting(requests: list[PendingRelease] | None = None) -> GateState:
    requests = requests if requests is not None else []
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: None,
        on_request=requests.append,
    )
    return request_approval(begin_gate(_release()), adapter)


# --- declaration contract -------------------------------------------------


def test_gated_mode_declaration_constants() -> None:
    assert RELEASE_MODE_GATED == "gated"
    assert DEFAULT_GATE_ENVIRONMENT == "release-gate"


def test_pending_release_validates_inputs() -> None:
    with pytest.raises(ValueError, match="version"):
        PendingRelease(version="  ")
    with pytest.raises(ValueError, match="environment"):
        PendingRelease(version="1.0.0", environment="bad;env")
    with pytest.raises(ValueError, match="head_sha"):
        PendingRelease(version="1.0.0", head_sha="not-a-sha")


def test_approval_decision_rejects_unknown_outcome() -> None:
    with pytest.raises(GateDecisionError, match="unknown approval outcome"):
        ApprovalDecision(outcome="maybe")


# --- pure transitions -------------------------------------------------------


def test_request_approval_moves_pending_to_awaiting() -> None:
    requests: list[PendingRelease] = []
    state = _awaiting(requests)
    assert state.phase is GatePhase.AWAITING_APPROVAL
    assert requests == [state.release]
    assert state.history == (GatePhase.PENDING, GatePhase.AWAITING_APPROVAL)


def test_request_approval_is_idempotent_while_awaiting() -> None:
    requests: list[PendingRelease] = []
    state = _awaiting(requests)
    again = request_approval(
        state,
        CallbackApprovalAdapter(
            on_check=lambda _release: None, on_request=requests.append
        ),
    )
    assert again is state
    assert len(requests) == 1


def test_request_approval_fails_closed_after_resolution() -> None:
    state = record_decision(_awaiting(), ApprovalDecision.denied())
    with pytest.raises(GateTransitionError, match="cannot request approval"):
        request_approval(state, CallbackApprovalAdapter(on_check=lambda _r: None))


def test_pending_decision_keeps_awaiting_unchanged() -> None:
    state = _awaiting()
    assert record_decision(state, ApprovalDecision.pending()) is state


def test_approve_and_deny_resolve_the_gate() -> None:
    awaiting = _awaiting()
    approved = record_decision(awaiting, ApprovalDecision.approved(by="alice"))
    assert approved.phase is GatePhase.APPROVED
    assert approved.decision is not None
    assert approved.decision.by == "alice"

    denied = record_decision(
        awaiting, ApprovalDecision.denied(by="bob", reason="bad build")
    )
    assert denied.phase is GatePhase.DENIED
    assert denied.decision is not None
    assert denied.decision.reason == "bad build"


def test_decision_on_non_awaiting_phase_fails_closed() -> None:
    with pytest.raises(GateTransitionError, match="cannot record a decision"):
        record_decision(begin_gate(_release()), ApprovalDecision.approved())

    published = finalize(
        record_decision(_awaiting(), ApprovalDecision.approved()),
        publish=lambda _release: None,
    )
    with pytest.raises(GateTransitionError, match="cannot record a decision"):
        record_decision(published, ApprovalDecision.denied())


def test_record_timeout_only_from_awaiting() -> None:
    timed_out = record_timeout(_awaiting())
    assert timed_out.phase is GatePhase.TIMED_OUT
    with pytest.raises(GateTransitionError, match="cannot time out"):
        record_timeout(timed_out)


# --- finalize ---------------------------------------------------------------


def test_finalize_approved_publishes_once() -> None:
    published: list[PendingRelease] = []
    state = record_decision(_awaiting(), ApprovalDecision.approved())
    result = finalize(state, publish=published.append)
    assert result.phase is GatePhase.PUBLISHED
    assert result.published is True
    assert published == [state.release]
    assert result.final is True

    # terminal states are sticky: re-finalizing does not publish twice
    assert finalize(result, publish=published.append) is result
    assert len(published) == 1


def test_finalize_approved_retry_skips_publish() -> None:
    published: list[PendingRelease] = []
    state = record_decision(_awaiting(), ApprovalDecision.approved())
    result = finalize(state, publish=published.append, already_published=True)
    assert result.phase is GatePhase.PUBLISHED
    assert result.published is True
    assert published == []


def test_finalize_denied_and_timed_out_abort_without_publish() -> None:
    published: list[PendingRelease] = []
    for decision_state in (
        record_decision(_awaiting(), ApprovalDecision.denied()),
        record_timeout(_awaiting()),
    ):
        result = finalize(decision_state, publish=published.append)
        assert result.phase is GatePhase.ABORTED
        assert result.published is False
        assert result.final is True
    assert published == []


def test_finalize_refuses_unresolved_gate() -> None:
    with pytest.raises(GateTransitionError, match="cannot finalize"):
        finalize(begin_gate(_release()), publish=lambda _release: None)
    with pytest.raises(GateTransitionError, match="cannot finalize"):
        finalize(_awaiting(), publish=lambda _release: None)


def test_finalize_approved_without_publish_fails_closed() -> None:
    state = record_decision(_awaiting(), ApprovalDecision.approved())
    with pytest.raises(GateTransitionError, match="no publish callable"):
        finalize(state)


# --- run_gated_release orchestrator -------------------------------------------


def _clock() -> tuple[list[float], object, object]:
    ticks = [0.0]

    def now() -> float:
        return ticks[0]

    def sleep(seconds: float) -> None:
        ticks[0] += seconds

    return ticks, now, sleep


def test_run_approve_path_publishes() -> None:
    requests: list[PendingRelease] = []
    decisions = iter(
        [
            ApprovalDecision.pending(),
            ApprovalDecision.pending(),
            ApprovalDecision.approved(by="alice"),
        ]
    )
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: next(decisions), on_request=requests.append
    )
    published: list[PendingRelease] = []
    ticks, now, sleep = _clock()

    state = run_gated_release(
        _release(),
        adapter=adapter,
        publish=published.append,
        timeout_seconds=600,
        poll_interval_seconds=5,
        now=now,
        sleep=sleep,
    )

    assert state.phase is GatePhase.PUBLISHED
    assert published == [state.release]
    assert requests == [state.release]
    assert state.history == (
        GatePhase.PENDING,
        GatePhase.AWAITING_APPROVAL,
        GatePhase.APPROVED,
        GatePhase.PUBLISHED,
    )
    assert ticks[0] == 10  # two pending polls, one interval each


def test_run_deny_path_aborts_without_publish() -> None:
    decisions = iter(
        [ApprovalDecision.pending(), ApprovalDecision.denied(by="bob", reason="nope")]
    )
    adapter = CallbackApprovalAdapter(on_check=lambda _release: next(decisions))
    published: list[PendingRelease] = []
    _, now, sleep = _clock()

    state = run_gated_release(
        _release(),
        adapter=adapter,
        publish=published.append,
        timeout_seconds=600,
        poll_interval_seconds=5,
        now=now,
        sleep=sleep,
    )

    assert state.phase is GatePhase.ABORTED
    assert published == []
    assert GatePhase.TIMED_OUT not in state.history


def test_run_timeout_aborts_instead_of_publishing() -> None:
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: ApprovalDecision.pending()
    )
    published: list[PendingRelease] = []
    ticks, now, sleep = _clock()

    state = run_gated_release(
        _release(),
        adapter=adapter,
        publish=published.append,
        timeout_seconds=30,
        poll_interval_seconds=10,
        now=now,
        sleep=sleep,
    )

    assert state.phase is GatePhase.ABORTED
    assert published == []
    assert GatePhase.TIMED_OUT in state.history
    assert ticks[0] == 30


def test_run_zero_timeout_still_reads_a_ready_decision() -> None:
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: ApprovalDecision.approved()
    )
    published: list[PendingRelease] = []
    _, now, sleep = _clock()

    state = run_gated_release(
        _release(),
        adapter=adapter,
        publish=published.append,
        timeout_seconds=0,
        poll_interval_seconds=5,
        now=now,
        sleep=sleep,
    )
    assert state.phase is GatePhase.PUBLISHED
    assert published == [state.release]


def test_run_zero_timeout_pending_aborts() -> None:
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: ApprovalDecision.pending()
    )
    _, now, sleep = _clock()
    state = run_gated_release(
        _release(),
        adapter=adapter,
        publish=lambda _release: None,
        timeout_seconds=0,
        poll_interval_seconds=5,
        now=now,
        sleep=sleep,
    )
    assert state.phase is GatePhase.ABORTED
    assert GatePhase.TIMED_OUT in state.history


def test_run_already_published_retry_does_not_republish() -> None:
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: ApprovalDecision.approved()
    )
    published: list[PendingRelease] = []
    _, now, sleep = _clock()

    state = run_gated_release(
        _release(),
        adapter=adapter,
        publish=published.append,
        poll_interval_seconds=5,
        now=now,
        sleep=sleep,
        already_published=True,
    )
    assert state.phase is GatePhase.PUBLISHED
    assert published == []


def test_run_rejects_bad_timing_parameters() -> None:
    adapter = CallbackApprovalAdapter(
        on_check=lambda _release: ApprovalDecision.approved()
    )
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_gated_release(
            _release(),
            adapter=adapter,
            publish=lambda _release: None,
            timeout_seconds=-1,
        )
    with pytest.raises(ValueError, match="poll_interval_seconds"):
        run_gated_release(
            _release(),
            adapter=adapter,
            publish=lambda _release: None,
            poll_interval_seconds=0,
        )


def test_run_propagates_decision_errors_loudly() -> None:
    def bogus_check(release: PendingRelease) -> ApprovalDecision:
        raise GateDecisionError("stale decision file")

    adapter = CallbackApprovalAdapter(on_check=bogus_check)
    with pytest.raises(GateDecisionError, match="stale decision"):
        run_gated_release(
            _release(),
            adapter=adapter,
            publish=lambda _release: None,
            timeout_seconds=0,
            poll_interval_seconds=5,
        )


# --- FileApprovalAdapter ------------------------------------------------------


def test_file_adapter_request_writes_marker(tmp_path: Path) -> None:
    adapter = FileApprovalAdapter(directory=tmp_path / "gate")
    release = _release()
    adapter.request(release)
    marker = tmp_path / "gate" / f"{DEFAULT_GATE_ENVIRONMENT}.request.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["version"] == "1.4.0"
    assert payload["environment"] == DEFAULT_GATE_ENVIRONMENT
    assert payload["head_sha"] == release.head_sha


def test_file_adapter_check_pending_without_decision(tmp_path: Path) -> None:
    adapter = FileApprovalAdapter(directory=tmp_path)
    assert adapter.check(_release()).outcome == "pending"


def _decision(tmp_path: Path, body: str) -> None:
    (tmp_path / f"{DEFAULT_GATE_ENVIRONMENT}.decision.json").write_text(
        body, encoding="utf-8"
    )


def test_file_adapter_approve_and_deny(tmp_path: Path) -> None:
    adapter = FileApprovalAdapter(directory=tmp_path)

    _decision(tmp_path, '{"outcome": "approved", "by": "alice"}')
    decision = adapter.check(_release())
    assert decision.outcome == "approved"
    assert decision.by == "alice"

    _decision(
        tmp_path,
        '{"outcome": "denied", "by": "bob", "reason": "regression"}',
    )
    decision = adapter.check(_release())
    assert decision.outcome == "denied"
    assert decision.by == "bob"
    assert decision.reason == "regression"


@pytest.mark.parametrize(
    "body,match",
    [
        ("not json", "not valid JSON"),
        ('["approved"]', "JSON object"),
        ('{"outcome": "maybe"}', "outcome"),
        ('{"outcome": "approved", "version": "9.9.9"}', "stale decision"),
        (
            '{"outcome": "approved", "environment": "other-env"}',
            "environment",
        ),
    ],
)
def test_file_adapter_rejects_untrustworthy_decisions(
    tmp_path: Path, body: str, match: str
) -> None:
    _decision(tmp_path, body)
    adapter = FileApprovalAdapter(directory=tmp_path)
    with pytest.raises(GateDecisionError, match=match):
        adapter.check(_release())


def test_file_adapter_pinned_version_decision_applies(tmp_path: Path) -> None:
    _decision(tmp_path, '{"outcome": "approved", "version": "1.4.0"}')
    adapter = FileApprovalAdapter(directory=tmp_path)
    assert adapter.check(_release("1.4.0")).outcome == "approved"


def test_full_file_adapter_flow_deny_is_idempotent(tmp_path: Path) -> None:
    """Deny through the shipped adapter; a retried run aborts identically."""
    adapter = FileApprovalAdapter(directory=tmp_path)
    _decision(tmp_path, '{"outcome": "denied", "version": "1.4.0"}')
    published: list[PendingRelease] = []
    _, now, sleep = _clock()

    for _ in range(2):  # run, then retry the run
        state = run_gated_release(
            _release(),
            adapter=adapter,
            publish=published.append,
            timeout_seconds=0,
            poll_interval_seconds=1,
            now=now,
            sleep=sleep,
        )
        assert state.phase is GatePhase.ABORTED
    assert published == []
