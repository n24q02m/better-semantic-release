"""
Gated release mode (`bsr.gating`, wave 2 item W2.1).

``release.mode = "gated"`` inserts a human approval gate between release
preparation and publish. On GitHub Actions the wait itself is provided by a
protected *environment* (reviewers approve the paused job); this module owns
the part that must stay auditable and identical in every context::

    PENDING -> AWAITING_APPROVAL -> APPROVED -> PUBLISHED
                                 -> DENIED   -> ABORTED
                                 -> TIMED_OUT-> ABORTED   (deadline hit pending)
    APPROVED | DENIED | TIMED_OUT -> ABORTED (gate resolved, publish skipped)

The machine is pure: a ``GateState`` plus transition functions. The approval
mechanism is injected behind ``ApprovalAdapter`` -- the shipped
``FileApprovalAdapter`` uses a directory on disk (drop a
``<env>.decision.json`` file to approve or deny), which keeps the state
machine exercisable end to end without any GitHub deployment API access.

Fail-closed invariants:

- publish is reachable only from ``APPROVED``;
- a gate that times out still pending never publishes -- it lands in
  ``ABORTED``, never ``PUBLISHED``;
- a stale decision file (wrong version, wrong environment, malformed JSON or
  unknown outcome) raises ``GateDecisionError`` instead of being silently
  applied;
- terminal states are sticky: ``finalize`` on a finished gate returns the
  state unchanged, and retried runs (``already_published=True``) mark the
  gate ``PUBLISHED`` without invoking the publish step a second time.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from semantic_release.bsr.errors import BsrGuardError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

RELEASE_MODE_GATED = "gated"
"""Value of ``[tool.semantic_release.bsr.release] mode`` selecting this flow."""

DEFAULT_GATE_ENVIRONMENT = "release-gate"
"""Default for ``[tool.semantic_release.bsr.gated] environment``."""

DEFAULT_GATE_TIMEOUT_SECONDS = 6 * 60 * 60
"""Gate wait bound (6h): a publish gate abandoned mid-run fails closed."""

DEFAULT_GATE_POLL_INTERVAL_SECONDS = 5.0
"""Interval between ``adapter.check`` polls while awaiting a decision."""

# GitHub environments allow letters, digits, `_`, `-`, ` `, `/` (255 chars max).
_ENVIRONMENT_PATTERN = re.compile(r"^[A-Za-z0-9_ \-/]{1,255}$")

_DECISION_OUTCOMES = ("pending", "approved", "denied")


class GateTransitionError(BsrGuardError):
    """An illegal state-machine transition was attempted."""


class GateDecisionError(BsrGuardError):
    """An approval decision could not be trusted (malformed or mismatched)."""


class GatePhase(str, Enum):
    """The phases of one gated release."""

    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting-approval"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed-out"
    PUBLISHED = "published"
    ABORTED = "aborted"


_AWAITING_OUTCOMES = {
    "approved": GatePhase.APPROVED,
    "denied": GatePhase.DENIED,
}

_TERMINAL_PHASES = frozenset({GatePhase.PUBLISHED, GatePhase.ABORTED})


@dataclass(frozen=True)
class PendingRelease:
    """The release candidate sitting on the gate."""

    version: str
    environment: str = DEFAULT_GATE_ENVIRONMENT
    head_sha: str = ""

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("PendingRelease.version must be non-empty")
        if not _ENVIRONMENT_PATTERN.match(self.environment):
            raise ValueError(
                f"PendingRelease.environment {self.environment!r} is not a "
                "valid GitHub environment name (letters, digits, '_', '-', "
                "'/', ' ' up to 255 chars)"
            )
        if self.head_sha and not re.fullmatch(r"[0-9a-fA-F]{7,64}", self.head_sha):
            raise ValueError(
                f"PendingRelease.head_sha {self.head_sha!r} is not a hex revision"
            )


@dataclass(frozen=True)
class ApprovalDecision:
    """One approval outcome read from an ``ApprovalAdapter``."""

    outcome: str  # "pending" | "approved" | "denied"
    by: str = ""
    at: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _DECISION_OUTCOMES:
            raise GateDecisionError(
                f"unknown approval outcome {self.outcome!r}; "
                f"expected one of {_DECISION_OUTCOMES}"
            )

    @classmethod
    def pending(cls) -> ApprovalDecision:
        """No decision recorded yet -- keep waiting."""
        return cls(outcome="pending")

    @classmethod
    def approved(cls, *, by: str = "", at: float | None = None) -> ApprovalDecision:
        """The environment was approved."""
        return cls(outcome="approved", by=by, at=at)

    @classmethod
    def denied(
        cls, *, by: str = "", at: float | None = None, reason: str = ""
    ) -> ApprovalDecision:
        """The environment was denied -- publish must not happen."""
        return cls(outcome="denied", by=by, at=at, reason=reason)


@dataclass(frozen=True)
class GateState:
    """Current phase of one gated release plus the audit trail."""

    release: PendingRelease
    phase: GatePhase = GatePhase.PENDING
    decision: ApprovalDecision | None = None
    published: bool = False
    history: tuple[GatePhase, ...] = (GatePhase.PENDING,)

    @property
    def final(self) -> bool:
        """True once the gate can no longer transition."""
        return self.phase in _TERMINAL_PHASES


def _transition(
    state: GateState,
    phase: GatePhase,
    *,
    decision: ApprovalDecision | None = None,
    published: bool | None = None,
) -> GateState:
    """Move ``state`` to ``phase`` appending the audit history."""
    return GateState(
        release=state.release,
        phase=phase,
        decision=decision if decision is not None else state.decision,
        published=state.published if published is None else published,
        history=(*state.history, phase),
    )


def begin_gate(release: PendingRelease) -> GateState:
    """Open a fresh gate for ``release`` (phase ``PENDING``)."""
    return GateState(release=release)


def request_approval(state: GateState, adapter: ApprovalAdapter) -> GateState:
    """
    Open the approval request on the gate (phase ``AWAITING_APPROVAL``).

    Idempotent: requesting approval on a gate that is already awaiting is a
    no-op so a retried run does not re-open (or clobber) the request.
    """
    if state.phase is GatePhase.AWAITING_APPROVAL:
        return state
    if state.phase is not GatePhase.PENDING:
        raise GateTransitionError(
            f"cannot request approval from phase {state.phase.value!r}"
        )
    adapter.request(state.release)
    return _transition(state, GatePhase.AWAITING_APPROVAL)


def record_decision(state: GateState, decision: ApprovalDecision) -> GateState:
    """
    Apply one adapter decision to an awaiting gate.

    ``pending`` keeps the gate in ``AWAITING_APPROVAL`` (no state change);
    ``approved``/``denied`` resolve it. Resolutions on any other phase are a
    transition error -- decisions cannot resurrect or rewrite a closed gate.
    """
    if state.phase is not GatePhase.AWAITING_APPROVAL:
        raise GateTransitionError(
            f"cannot record a decision in phase {state.phase.value!r}"
        )
    if decision.outcome == "pending":
        return state
    return _transition(state, _AWAITING_OUTCOMES[decision.outcome], decision=decision)


def record_timeout(state: GateState) -> GateState:
    """Expire an awaiting gate past its deadline (phase ``TIMED_OUT``)."""
    if state.phase is not GatePhase.AWAITING_APPROVAL:
        raise GateTransitionError(f"cannot time out phase {state.phase.value!r}")
    return _transition(state, GatePhase.TIMED_OUT)


def finalize(
    state: GateState,
    *,
    publish: Callable[[PendingRelease], None] | None = None,
    already_published: bool = False,
) -> GateState:
    """
    Close the gate: publish when approved, otherwise mark it aborted.

    - ``APPROVED`` -> ``PUBLISHED`` via ``publish(release)``. With
      ``already_published=True`` the publish step is skipped and the gate is
      still marked ``PUBLISHED`` -- this is the idempotent-retry rule: a
      rerun of the pipeline re-marks the gate without publishing twice.
    - ``DENIED``/``TIMED_OUT`` -> ``ABORTED`` (no publish, ever).
    - ``PUBLISHED``/``ABORTED`` -> returned unchanged (terminal, sticky).
    - ``PENDING``/``AWAITING_APPROVAL`` -> ``GateTransitionError``: you cannot
      finalize a gate that has not resolved.
    """
    if state.final:
        return state
    if state.phase in (GatePhase.PENDING, GatePhase.AWAITING_APPROVAL):
        raise GateTransitionError(
            f"cannot finalize a gate still in phase {state.phase.value!r}; "
            "wait for a decision or record a timeout first"
        )
    if state.phase in (GatePhase.DENIED, GatePhase.TIMED_OUT):
        return _transition(state, GatePhase.ABORTED)
    if state.phase is not GatePhase.APPROVED:
        raise GateTransitionError(  # pragma: no cover - defensive
            f"unknown gate phase {state.phase.value!r}"
        )
    if already_published:
        return _transition(state, GatePhase.PUBLISHED, published=True)
    if publish is None:
        raise GateTransitionError(
            "gate approved but no publish callable was provided; "
            "refusing to mark the gate published without publishing"
        )
    publish(state.release)
    return _transition(state, GatePhase.PUBLISHED, published=True)


@runtime_checkable
class ApprovalAdapter(Protocol):
    """
    The approval mechanism behind the gate.

    ``request`` opens the gate for reviewers (called exactly once per run);
    ``check`` polls the current decision and must return
    ``ApprovalDecision.pending()`` while no reviewer has acted. On GitHub
    Actions the platform's environment-protection wait IS the gate; adapters
    model it so the machine stays testable without cloud dependencies.
    """

    def request(self, release: PendingRelease) -> None:
        """Open the approval request for ``release``."""
        ...

    def check(self, release: PendingRelease) -> ApprovalDecision:
        """Poll the current decision for ``release``."""
        ...


@dataclass(frozen=True)
class CallbackApprovalAdapter:
    """
    Adapter built from two callables -- the test/demo injection point.

    ``on_request`` is optional; ``on_check`` must return an
    ``ApprovalDecision`` (or ``None``, treated as pending).
    """

    on_check: Callable[[PendingRelease], ApprovalDecision | None]
    on_request: Callable[[PendingRelease], None] | None = None

    def request(self, release: PendingRelease) -> None:
        if self.on_request is not None:
            self.on_request(release)

    def check(self, release: PendingRelease) -> ApprovalDecision:
        decision = self.on_check(release)
        return ApprovalDecision.pending() if decision is None else decision


@dataclass(frozen=True)
class FileApprovalAdapter:
    """
    Filesystem gate: approvals land as a decision file in ``directory``.

    ``request`` writes ``<environment>.request.json`` (the auditable marker
    that a gate was opened); ``check`` reads ``<environment>.decision.json``::

        {"outcome": "approved", "by": "alice", "version": "1.4.0"}
        {"outcome": "denied", "by": "bob", "reason": "regression found"}

    ``outcome`` is required and must be ``approved`` or ``denied``. ``version``
    and ``environment`` are optional; when present they must equal the pending
    release's, or the decision is rejected -- a stale deny for the previous
    cut cannot abort the current one (and a stale approve cannot publish it).
    Every malformed/mismatched decision raises ``GateDecisionError`` so the
    run fails loud instead of silently ignoring (or silently applying) it.
    """

    directory: Path

    def _request_path(self, release: PendingRelease) -> Path:
        return self.directory / f"{release.environment}.request.json"

    def _decision_path(self, release: PendingRelease) -> Path:
        return self.directory / f"{release.environment}.decision.json"

    def request(self, release: PendingRelease) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._request_path(release).write_text(
            json.dumps(
                {
                    "version": release.version,
                    "environment": release.environment,
                    "head_sha": release.head_sha,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def check(self, release: PendingRelease) -> ApprovalDecision:
        path = self._decision_path(release)
        if not path.is_file():
            return ApprovalDecision.pending()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GateDecisionError(
                f"decision file {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise GateDecisionError(f"decision file {path} must contain a JSON object")
        outcome = raw.get("outcome")
        if outcome not in ("approved", "denied"):
            raise GateDecisionError(
                f"decision file {path} has outcome {outcome!r}; "
                "expected 'approved' or 'denied'"
            )
        version = raw.get("version")
        if version is not None and version != release.version:
            raise GateDecisionError(
                f"decision file {path} targets version {version!r} but the "
                f"gate is for {release.version!r}; refusing a stale decision"
            )
        environment = raw.get("environment")
        if environment is not None and environment != release.environment:
            raise GateDecisionError(
                f"decision file {path} targets environment {environment!r} "
                f"but the gate is on {release.environment!r}"
            )
        if outcome == "approved":
            return ApprovalDecision.approved(by=str(raw.get("by", "")))
        return ApprovalDecision.denied(
            by=str(raw.get("by", "")), reason=str(raw.get("reason", ""))
        )


def run_gated_release(
    release: PendingRelease,
    *,
    adapter: ApprovalAdapter,
    publish: Callable[[PendingRelease], None],
    timeout_seconds: float = DEFAULT_GATE_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_GATE_POLL_INTERVAL_SECONDS,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    already_published: bool = False,
) -> GateState:
    """
    Drive one release through the gate and return the final ``GateState``.

    The loop polls ``adapter.check`` until the decision resolves or the
    deadline passes; a decision that lands exactly on the deadline still
    counts (approval at the edge wins over timeout). ``now`` and ``sleep``
    are injected so tests run the full machine with a virtual clock and a
    fake adapter -- the same code path Actions runs with a real adapter.

    The returned state is either ``PUBLISHED`` (approved, or already
    published upstream of the gate) or ``ABORTED`` (denied / timed out).
    Publish exceptions propagate so a failed publish can be retried by
    re-running; the gate stays ``APPROVED`` in the caller's eyes.
    """
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be >= 0")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be > 0")

    state = request_approval(begin_gate(release), adapter)
    deadline = now() + timeout_seconds
    while state.phase is GatePhase.AWAITING_APPROVAL:
        state = record_decision(state, adapter.check(state.release))
        if state.phase is not GatePhase.AWAITING_APPROVAL:
            break
        if now() >= deadline:
            state = record_timeout(state)
            break
        sleep(poll_interval_seconds)
    return finalize(state, publish=publish, already_published=already_published)


__all__ = [
    "DEFAULT_GATE_ENVIRONMENT",
    "DEFAULT_GATE_POLL_INTERVAL_SECONDS",
    "DEFAULT_GATE_TIMEOUT_SECONDS",
    "RELEASE_MODE_GATED",
    "ApprovalAdapter",
    "ApprovalDecision",
    "CallbackApprovalAdapter",
    "FileApprovalAdapter",
    "GateDecisionError",
    "GatePhase",
    "GateState",
    "GateTransitionError",
    "PendingRelease",
    "begin_gate",
    "finalize",
    "record_decision",
    "record_timeout",
    "request_approval",
    "run_gated_release",
]
