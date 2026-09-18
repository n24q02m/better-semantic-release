"""
`semantic-release plan` (bsr): read-only release-plan projection.

Task 2 of the universal-release-engine plan: a command that shows exactly what
`version` WOULD do — the bump, the components, the blockers — without touching
the repository, the version declarations, the registry, or the VCS.

The command never mutates anything. Its only filesystem write is the plan file
requested through ``--write``. Under ``--format json`` stdout carries exactly
one machine-readable document (schema v1, `bsr.plan`), emitted from a
close-callback so every exit path prints the document.

Determinism contract: the default plan is fully offline (no registry probe),
so ``--strict`` exit codes are stable for a fixed repository state. Registry
collision checks run only under ``--check-registry``; ``version`` keeps
enforcing them live before any persistence.

Task 3 moved the state pipeline into `bsr.preflight` so `verify` and `doctor`
consume the SAME computation — the three commands must never drift.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

from semantic_release.bsr.config import load_bsr_config
from semantic_release.bsr.doctor import (
    _LIVE_PROBE,
    SEVERITY_BLOCKER,
    STATUS_SKIP,
    check_registry_state,
)
from semantic_release.bsr.guards import resolve_registry
from semantic_release.bsr.plan import Blocker, build_release_plan
from semantic_release.bsr.preflight import compute_release_state

if TYPE_CHECKING:  # pragma: no cover
    from semantic_release.cli.cli_context import CliContextObj

FORMAT_TABLE = "table"
FORMAT_MARKDOWN = "markdown"
FORMAT_JSON = "json"

# No-release reasons that stay exit-0 under --strict, mirroring `version`
# (which only escalates the orphaned-history case). ORPHAN without the guard
# enabled is a real danger signal, so it is NOT in this set.
_BENIGN_NO_RELEASE_REASONS = {"NO_QUALIFYING_COMMITS", "ALREADY_RELEASED_NOOP"}


def _emit_plan(doc: Any, output_format: str, write_path: str | None) -> None:
    """Render the plan document for the requested output format."""
    text_out: str | None = None
    if output_format == FORMAT_JSON:
        click.echo(json.dumps(doc.to_document(), indent=2))
    elif output_format == FORMAT_MARKDOWN:
        text_out = doc.render_markdown()
    else:
        text_out = doc.render_table()
    if text_out is not None:
        click.echo(text_out)
        if write_path is not None:
            with open(write_path, "w", encoding="utf-8") as fh:  # noqa: PTH123
                fh.write(text_out)
                fh.write("\n")


@click.command(name="plan")
@click.option(
    "--format",
    "output_format",
    type=click.Choice([FORMAT_TABLE, FORMAT_MARKDOWN, FORMAT_JSON]),
    default=FORMAT_TABLE,
    help=(
        "Output format. 'table' and 'markdown' render the plan for humans "
        "(stdout), 'json' prints one machine-readable document on stdout."
    ),
)
@click.option(
    "--write",
    "write_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Also write the rendered plan to this file (e.g. release-plan.md).",
)
@click.option(
    "--check-registry",
    "check_registry",
    is_flag=True,
    default=False,
    help=(
        "Also probe the configured registry for the computed version. Off by "
        "default so the plan (and its --strict exit code) is deterministic "
        "offline; the `version` command still enforces the guard live."
    ),
)
@click.option(
    "--strict",
    "strict",
    is_flag=True,
    default=False,
    help="Exit non-zero when the plan is blocked (guard trips recorded as blockers).",
)
@click.pass_obj
def plan(
    cli_ctx: CliContextObj,
    output_format: str = FORMAT_TABLE,
    write_path: str | None = None,
    check_registry: bool = False,
    strict: bool = False,
) -> None:
    """
    Compute the release plan for the current repository state, read-only.

    Unlike `version`, nothing is committed, tagged, pushed, or written to
    version declarations. The plan reports what a plain `version` dispatch
    (no forced level) would do: the new version or the no-release reason,
    per-component rows when a monorepo component map is configured, and the
    state of the enabled release-safety guards.
    """
    ctx = click.get_current_context()
    runtime = cli_ctx.runtime_ctx
    config = cli_ctx.raw_config
    opts = runtime.global_cli_options

    _bsr_cfg = load_bsr_config(opts.config_file)
    state = compute_release_state(runtime=runtime, config=config, bsr_cfg=_bsr_cfg)

    _plan_state: dict[str, Any] = {
        "released": False,
        "version": str(state.new_version),
        "tag": state.new_version.as_tag(),
        "previous_version": state.previous_version,
        "decision": state.decision,
        "bump_stats": state.bump_stats,
        "components": state.components,
        "blockers": (),
        "registry": None,
    }

    def _emit() -> None:
        doc = build_release_plan(
            released=_plan_state["released"],
            version=_plan_state["version"],
            tag=_plan_state["tag"],
            previous_version=_plan_state["previous_version"],
            decision=_plan_state["decision"],
            bump_stats=_plan_state["bump_stats"],
            components=_plan_state["components"],
            blockers=_plan_state["blockers"],
            registry=_plan_state["registry"],
        )
        _emit_plan(doc, output_format, write_path)

    ctx.call_on_close(_emit)

    # Orphaned-history silent-freeze: guard trip becomes a blocker row.
    if (
        state.decision is not None
        and state.decision.reason == "ORPHAN"
        and _bsr_cfg.guard_orphan_tag
    ):
        _plan_state["blockers"] = (
            Blocker(
                code="ORPHAN_TAG",
                message=(
                    f"{state.new_version!s} is orphaned: higher than every version "
                    "reachable from HEAD (rewritten history?)"
                ),
                remediation="verify tag reachability; do not re-cut a consumed version",
            ),
        )

    # Release-safety guards, read-only: trip => blocker rows, not an abort.
    # The registry probe is OPT-IN (--check-registry): a live registry probe is
    # network-nondeterministic (transient UNKNOWN flips `--strict` between
    # exit 1 and 0), so the default plan is fully offline. `version` still
    # enforces the guard live right before any persistence.
    if check_registry and _bsr_cfg.guard_registry_collision:
        project_name = runtime.project_metadata.get("name", "") or ""
        check = check_registry_state(
            _bsr_cfg,
            project_name,
            str(state.new_version),
            probe=_LIVE_PROBE,
        )
        if check.failed() and check.severity == SEVERITY_BLOCKER:
            _plan_state["blockers"] = (
                *_plan_state["blockers"],
                Blocker(
                    code=check.code,
                    message=check.what,
                    remediation=check.fix,
                ),
            )
        if check.status == STATUS_SKIP:
            _plan_state["registry"] = None
        else:
            _plan_state["registry"] = {
                "registry": resolve_registry(_bsr_cfg, project_name),
                "reachable": check.status == "pass",
                "detail": check.what,
            }

    _plan_state["released"] = state.decision is None

    blocked = bool(_plan_state["blockers"]) or (
        state.decision is not None
        and state.decision.reason not in _BENIGN_NO_RELEASE_REASONS
    )
    if strict and blocked:
        ctx.exit(1)
