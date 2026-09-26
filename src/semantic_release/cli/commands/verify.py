"""
`semantic-release verify` (bsr): enforce the release-safety policy, read-only.

Task 3: `verify` runs the policy/safety blocker layer of the doctor catalog
against the current repository state. It never mutates anything; it answers
one question — "would a publish be safe right now?" — and exits non-zero when
any policy blocker trips.

Unlike `plan` (offline by default), `verify` probes the registry LIVE by
default: it is the pre-flight gate, so fail-closed registry semantics are the
point. Use `--offline` for deterministic CI runs.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import click
from git import Repo
from git.exc import GitCommandError

from semantic_release.bsr.config import load_bsr_config
from semantic_release.bsr.doctor import (
    _LIVE_PROBE,
    SEVERITY_BLOCKER,
    STATUS_FAIL,
    CheckResult,
    DoctorReport,
    check_branch_config,
    check_hvcs_token,
    check_missing_remote,
    check_registry_state,
)
from semantic_release.bsr.plan_drift import (
    DriftFinding,
    PlanSnapshotError,
    load_plan_snapshot,
    verify_plan_drift,
)
from semantic_release.bsr.preflight import compute_release_state
from semantic_release.cli.config import RuntimeContext
from semantic_release.errors import MissingGitRemote, NotAReleaseBranch

if TYPE_CHECKING:  # pragma: no cover
    from click import Context

    from semantic_release.bsr.doctor import DoctorReport as _DoctorReport  # noqa: F401
    from semantic_release.bsr.plan import ReleasePlan
    from semantic_release.cli.cli_context import CliContextObj


def _active_branch(repo_dir: object) -> str:
    with Repo(str(repo_dir)) as repo:
        return str(repo.active_branch)


def _plan_drift_checks(
    *,
    plan_snapshot: ReleasePlan | None,
    plan_error: str | None,
    repo_dir: object,
    current_version: str,
) -> list[CheckResult]:
    """Build the W1.4 plan-snapshot drift rows (fail closed on any mismatch)."""
    if plan_error is not None:
        return [
            CheckResult(
                code="PLAN_SNAPSHOT_INVALID",
                severity=SEVERITY_BLOCKER,
                status=STATUS_FAIL,
                what="plan snapshot could not be consumed",
                why=plan_error,
                fix="re-run `plan --write` to produce a valid snapshot",
            )
        ]
    if plan_snapshot is None:
        return []

    with Repo(str(repo_dir)) as repo:
        try:
            head_sha: str | None = str(repo.head.commit.hexsha)
        except ValueError:
            head_sha = None
        existing_tags = {tag.name for tag in repo.tags}
    findings = verify_plan_drift(
        plan_snapshot,
        head_sha=head_sha or "",
        current_version=current_version,
        planned_tag_exists=(
            plan_snapshot.tag is not None and plan_snapshot.tag in existing_tags
        ),
    )
    if head_sha is None:
        findings = (
            DriftFinding(
                code="PLAN_HEAD_DRIFT",
                message="plan has a head anchor but the repository has no commits",
                remediation="re-run `plan --write` on the intended repository",
            ),
            *findings,
        )
    return [
        CheckResult(
            code=finding.code,
            severity=SEVERITY_BLOCKER,
            status=STATUS_FAIL,
            what=finding.message,
            why="the plan snapshot no longer matches the repository state",
            fix=finding.remediation,
        )
        for finding in findings
    ]


def _print_report(report: DoctorReport, output_format: str) -> None:
    if output_format == "json":
        click.echo(json.dumps(report.to_document(), indent=2))
    elif output_format == "markdown":
        click.echo(report.render_markdown())
    else:
        click.echo(report.render_table())


@click.command(name="verify")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "markdown", "json"]),
    default="table",
    help="Output format for the check report.",
)
@click.option(
    "--offline",
    "offline",
    is_flag=True,
    default=False,
    help="Skip the live registry probe (registry checks report SKIP instead).",
)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Check a `plan --write` snapshot against the current repository state "
        "and fail closed on drift (head moved, version changed, or the "
        "planned tag already exists)."
    ),
)
@click.pass_obj
def verify(
    cli_ctx: CliContextObj,
    output_format: str = "table",
    offline: bool = False,
    plan_path: str | None = None,
) -> None:
    """
    Enforce the release-safety policy against the current repository state.

    Runs the blocker-severity checks (orphan tag, registry collision/unknown,
    missing remote, branch config, HVCS token) and exits 1 when any of them
    fails. Nothing is committed, tagged, or pushed.
    """
    ctx: Context = click.get_current_context()
    raw = cli_ctx.raw_config
    checks: list[CheckResult] = []
    try:
        runtime = RuntimeContext.from_raw_config(
            raw, global_cli_options=cli_ctx.global_opts
        )
    except (NotAReleaseBranch, GitCommandError, MissingGitRemote) as exc:
        # Runtime construction refuses non-release branches and dies on remote
        # probing; both are exactly what verify must REPORT and fail closed on.
        with Repo(str(raw.repo_dir)) as repo:
            checks.append(check_missing_remote(repo))
        active = str(repo.active_branch) if not repo.bare else "(bare)"
        checks.append(
            check_branch_config(
                tuple(cfg.match for cfg in raw.branches.values()),
                active,
            )
        )
        if not any(c.code == "BRANCH_CONFIG" and c.status == "fail" for c in checks):
            # The failure was remote/other; surface it as the blocked row.
            checks.append(
                CheckResult(
                    code="RUNTIME_CONFIG",
                    severity=SEVERITY_BLOCKER,
                    status=STATUS_FAIL,
                    what="runtime context could not be constructed",
                    why=str(exc),
                    fix="fix the reported git/config problem and re-run",
                )
            )
        report = DoctorReport(checks=tuple(checks))
        _print_report(report, output_format)
        ctx.exit(1)
        return

    _bsr_cfg = load_bsr_config(cli_ctx.global_opts.config_file)

    # W1.4: optional plan-snapshot consumption (`verify --plan <path>`).
    plan_snapshot: ReleasePlan | None = None
    plan_error: str | None = None
    if plan_path is not None:
        try:
            plan_snapshot = load_plan_snapshot(plan_path)
        except PlanSnapshotError as exc:
            plan_error = str(exc)

    state = compute_release_state(runtime=runtime, config=raw, bsr_cfg=_bsr_cfg)

    with Repo(str(runtime.repo_dir)) as repo:
        checks.append(check_missing_remote(repo))

    checks.append(
        check_branch_config(
            tuple(cfg.match for cfg in raw.branches.values()),
            _active_branch(runtime.repo_dir),
        )
    )

    # Orphaned-history silent-freeze: the dangerous no-release shape.
    if state.decision is not None and state.decision.reason == "ORPHAN":
        checks.append(
            CheckResult(
                code="ORPHAN_TAG",
                severity=SEVERITY_BLOCKER,
                status=STATUS_FAIL,
                what=f"{state.new_version!s} is orphaned (rewritten history?)",
                why="the computed version is higher than everything reachable from HEAD",
                fix="verify tag reachability; do not re-cut a consumed version",
            )
        )

    # W1.4: plan-snapshot drift rows (fail closed on any mismatch).
    checks.extend(
        _plan_drift_checks(
            plan_snapshot=plan_snapshot,
            plan_error=plan_error,
            repo_dir=runtime.repo_dir,
            current_version=str(state.new_version),
        )
    )

    checks.append(check_hvcs_token(runtime.hvcs_client, needs_release=True))

    project_name = runtime.project_metadata.get("name", "") or ""
    checks.append(
        check_registry_state(
            _bsr_cfg,
            project_name,
            str(state.new_version),
            probe=None if offline else _LIVE_PROBE,
        )
    )

    report = DoctorReport(checks=tuple(checks))
    _print_report(report, output_format)
    if report.blocked:
        ctx.exit(1)
