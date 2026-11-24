"""Stateless recovery diagnostics for partial releases (`bsr verify --repair-check`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git import Repo

from semantic_release.bsr.doctor import (
    _LIVE_PROBE,
    SEVERITY_BLOCKER,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIP,
    CheckResult,
)
from semantic_release.bsr.guards import resolve_registry
from semantic_release.bsr.registry import ProbeResult

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from semantic_release.bsr.config import BsrConfig
    from semantic_release.bsr.doctor import RegistryProbe


def _has_local_tag(repo_dir: Path, tag_name: str) -> bool:
    """Return True when ``tag_name`` exists in local git tags."""
    with Repo(str(repo_dir)) as repo:
        return tag_name in {tag.name for tag in repo.tags}


def _probe_target_registry(
    bsr_config: BsrConfig,
    project_name: str,
    target_version: str,
    probe: RegistryProbe | None,
) -> tuple[str, ProbeResult | None]:
    """Resolve registry name and invoke probe when live checks are enabled."""
    registry = resolve_registry(bsr_config, project_name)
    if registry == "none" or probe is None:
        return registry, None
    return registry, probe(registry, project_name, target_version)


def _registry_row(
    registry: str,
    project_name: str,
    target_version: str,
    probe_result: ProbeResult | None,
) -> CheckResult:
    """Format the ``REPAIR_REGISTRY_STATE`` row for the resolved probe outcome."""
    if probe_result is None:
        reason = (
            "registry is set to none"
            if registry == "none"
            else "offline mode skipped live registry probe"
        )
        return CheckResult(
            code="REPAIR_REGISTRY_STATE",
            severity=SEVERITY_INFO,
            status=STATUS_SKIP,
            what=f"registry probe skipped ({registry})",
            why=reason,
        )
    if probe_result is ProbeResult.UNKNOWN:
        return CheckResult(
            code="REPAIR_REGISTRY_STATE",
            severity=SEVERITY_WARNING,
            status=STATUS_FAIL,
            what=f"{registry} probe for {project_name}@{target_version} returned unknown",
            why="transient error or missing endpoint credentials prevented confirmation",
            fix="check network/credentials and re-run without --offline",
        )
    return CheckResult(
        code="REPAIR_REGISTRY_STATE",
        severity=SEVERITY_INFO,
        status=STATUS_PASS,
        what=f"{registry} state for {project_name}@{target_version}: {probe_result.value}",
    )


def _next_action_row(
    *,
    tag_present: bool,
    tag_name: str,
    registry: str,
    probe_result: ProbeResult | None,
    target_version: str,
) -> CheckResult:
    """Derive a stateless recovery recommendation from tag + registry observations."""
    if probe_result is ProbeResult.UNKNOWN:
        return CheckResult(
            code="REPAIR_NEXT_ACTION",
            severity=SEVERITY_BLOCKER,
            status=STATUS_FAIL,
            what=f"cannot determine safe repair action for {tag_name}",
            why=f"{registry} probe was inconclusive",
            fix="verify registry reachability and re-run bsr verify --repair-check",
        )
    if tag_present and probe_result is ProbeResult.FREE:
        return CheckResult(
            code="REPAIR_NEXT_ACTION",
            severity=SEVERITY_WARNING,
            status=STATUS_FAIL,
            what=f"partial release detected: {tag_name} exists locally but {registry} lacks {target_version}",
            why="git tag creation completed before registry publishing finished",
            fix=f"resume or re-run semantic-release publish for {tag_name}",
        )
    if not tag_present and probe_result is ProbeResult.EXISTS:
        return CheckResult(
            code="REPAIR_NEXT_ACTION",
            severity=SEVERITY_BLOCKER,
            status=STATUS_FAIL,
            what=f"partial release detected: {registry} has {target_version} without tag {tag_name}",
            why="artifact exists on registry while local/fetched git history lacks matching tag",
            fix=f"fetch remote tags or tag released commit as {tag_name} before next bump",
        )
    if tag_present and probe_result is ProbeResult.EXISTS:
        return CheckResult(
            code="REPAIR_NEXT_ACTION",
            severity=SEVERITY_INFO,
            status=STATUS_PASS,
            what=f"release {tag_name} is reconciled on {registry}",
            why=f"local tag {tag_name} and {registry} artifact {target_version} both exist",
        )
    return CheckResult(
        code="REPAIR_NEXT_ACTION",
        severity=SEVERITY_INFO,
        status=STATUS_PASS,
        what=f"no partial release detected for {tag_name}",
        why=f"tag {tag_name} is absent locally and no conflicting artifact blocks publish",
    )


def evaluate_repair_status(
    *,
    repo_dir: Path,
    bsr_config: BsrConfig,
    project_name: str,
    target_version: str,
    tag_name: str,
    probe: RegistryProbe | None = _LIVE_PROBE,
) -> tuple[CheckResult, ...]:
    """
    Inspect local git tags and registry probe state without persisting a state file.

    Returns three :class:`CheckResult` rows: ``REPAIR_LOCAL_TAG``,
    ``REPAIR_REGISTRY_STATE``, and ``REPAIR_NEXT_ACTION``.
    """
    tag_present = _has_local_tag(repo_dir, tag_name)
    tag_row = CheckResult(
        code="REPAIR_LOCAL_TAG",
        severity=SEVERITY_INFO,
        status=STATUS_PASS,
        what=(
            f"local tag {tag_name} is present"
            if tag_present
            else f"local tag {tag_name} is absent"
        ),
    )
    registry, probe_result = _probe_target_registry(
        bsr_config, project_name, target_version, probe
    )
    registry_row = _registry_row(registry, project_name, target_version, probe_result)
    action_row = _next_action_row(
        tag_present=tag_present,
        tag_name=tag_name,
        registry=registry,
        probe_result=probe_result,
        target_version=target_version,
    )
    return (tag_row, registry_row, action_row)
