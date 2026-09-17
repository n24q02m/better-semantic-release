"""
`semantic-release doctor` (bsr): full readiness diagnostics, read-only.

Task 3: `doctor` runs the WHOLE check catalog — policy/safety blockers and
diagnostics/warnings — and renders a structured report. It is a superset of
`verify`: readiness, not just permission.

Offline by default (registry checks SKIP); pass `--check-registry` to probe
live. Exit code is 0 unless `--strict` is passed, in which case a blocked
report exits 1.
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
    DoctorReport,
    check_branch_config,
    check_hvcs_token,
    check_missing_remote,
    check_prerelease_consistency,
    check_registry_state,
    check_tag_format,
    check_trusted_publishing,
    check_version_consistency,
    check_version_targets,
)
from semantic_release.bsr.preflight import compute_release_state
from semantic_release.bsr.version_sources import resolve_version_sources
from semantic_release.cli.config import RuntimeContext
from semantic_release.errors import MissingGitRemote, NotAReleaseBranch

if TYPE_CHECKING:  # pragma: no cover
    from click import Context

    from semantic_release.cli.cli_context import CliContextObj


def _active_branch(repo_dir: object) -> str:
    with Repo(str(repo_dir)) as repo:
        return str(repo.active_branch)


def _print_report(report: DoctorReport, output_format: str) -> None:
    if output_format == "json":
        click.echo(json.dumps(report.to_document(), indent=2))
    elif output_format == "markdown":
        click.echo(report.render_markdown())
    else:
        click.echo(report.render_table())


@click.command(name="doctor")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "markdown", "json"]),
    default="table",
    help="Output format for the diagnostics report.",
)
@click.option(
    "--check-registry",
    "check_registry",
    is_flag=True,
    default=False,
    help="Probe the configured registry for the computed version (live).",
)
@click.option(
    "--strict",
    "strict",
    is_flag=True,
    default=False,
    help="Exit 1 when the report is blocked by any policy check.",
)
@click.pass_obj
def doctor(
    cli_ctx: CliContextObj,
    output_format: str = "table",
    check_registry: bool = False,
    strict: bool = False,
) -> None:
    """
    Diagnose release readiness across the full check catalog.

    Covers blockers (remote, branch config, orphan tag, registry) and
    warnings (tag format drift, HVCS token, prerelease consistency, version
    declaration drift). Nothing is committed, tagged, or pushed.
    """
    ctx: Context = click.get_current_context()
    raw = cli_ctx.raw_config
    checks: list = []
    try:
        runtime = RuntimeContext.from_raw_config(
            raw, global_cli_options=cli_ctx.global_opts
        )
    except (NotAReleaseBranch, GitCommandError, MissingGitRemote) as exc:
        # Runtime construction refuses non-release branches and dies on remote
        # probing; doctor reports both instead of crashing.
        del exc  # the branch/remote check rows carry the diagnosis
        with Repo(str(raw.repo_dir)) as repo:
            checks.append(check_missing_remote(repo))
        active = str(repo.active_branch) if not repo.bare else "(bare)"
        checks.append(
            check_branch_config(
                tuple(cfg.match for cfg in raw.branches.values()),
                active,
            )
        )
        report = DoctorReport(checks=tuple(checks))
        _print_report(report, output_format)
        if strict and report.blocked:
            ctx.exit(1)
        return
    _bsr_cfg = load_bsr_config(cli_ctx.global_opts.config_file)

    state = compute_release_state(runtime=runtime, config=raw, bsr_cfg=_bsr_cfg)
    active = _active_branch(runtime.repo_dir)

    with Repo(str(runtime.repo_dir)) as repo:
        checks.append(check_missing_remote(repo))
        checks.append(check_tag_format(repo, runtime.version_translator))

    branch_matches = tuple(cfg.match for cfg in raw.branches.values())
    checks.append(check_branch_config(branch_matches, active))

    import re  # noqa: PLC0415

    active_cfg = next(
        (
            cfg
            for cfg in raw.branches.values()
            if re.search(cfg.match, active)
        ),
        None,
    )
    active_prerelease = active_cfg.prerelease if active_cfg is not None else runtime.prerelease
    checks.append(
        check_prerelease_consistency(active_prerelease, state.new_version)
    )

    checks.append(check_hvcs_token(runtime.hvcs_client, needs_release=False))
    checks.append(check_version_targets(runtime, state.previous_version))

    version_sources = resolve_version_sources(
        repo_dir=runtime.repo_dir,
        translator=runtime.version_translator,
        declarations=runtime.version_declarations,
        source_configs=(_bsr_cfg.version.sources if _bsr_cfg.version else ()),
    )
    checks.append(check_version_consistency(version_sources))

    checks.append(check_trusted_publishing(_bsr_cfg))

    project_name = runtime.project_metadata.get("name", "") or ""
    checks.append(
        check_registry_state(
            _bsr_cfg,
            project_name,
            str(state.new_version),
            probe=_LIVE_PROBE if check_registry else None,
        )
    )

    report = DoctorReport(checks=tuple(checks))
    _print_report(report, output_format)
    if strict and report.blocked:
        ctx.exit(1)
