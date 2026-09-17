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
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import click
from git import Repo

from semantic_release.bsr.config import load_bsr_config
from semantic_release.bsr.explain import BumpStats, classify_no_release
from semantic_release.bsr.guards import is_orphaned_recompute, resolve_registry
from semantic_release.bsr.path_filter import make_path_filter
from semantic_release.bsr.plan import Blocker, build_release_plan
from semantic_release.bsr.registry import ProbeResult, probe_registry
from semantic_release.bsr.summary import build_summary, resolve_components
from semantic_release.cli.commands.version import (
    is_forced_prerelease,
    last_released,
)
from semantic_release.version.algorithm import next_version, tags_and_versions

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path
    from typing import Mapping

    from semantic_release.bsr.config import BsrConfig
    from semantic_release.bsr.explain import ReleaseDecision
    from semantic_release.cli.cli_context import CliContextObj
    from semantic_release.cli.config import RuntimeContext
    from semantic_release.enums import LevelBump
    from semantic_release.version.translator import VersionTranslator
    from semantic_release.version.version import Version

FORMAT_TABLE = "table"
FORMAT_MARKDOWN = "markdown"
FORMAT_JSON = "json"

# No-release reasons that stay exit-0 under --strict, mirroring `version`
# (which only escalates the orphaned-history case). ORPHAN without the guard
# enabled is a real danger signal, so it is NOT in this set.
_BENIGN_NO_RELEASE_REASONS = {"NO_QUALIFYING_COMMITS", "ALREADY_RELEASED_NOOP"}


def _no_release_blocker(new_version: Version) -> Blocker:
    return Blocker(
        code="ORPHAN_TAG",
        message=(
            f"{new_version!s} is orphaned: higher than every version "
            "reachable from HEAD (rewritten history?)"
        ),
        remediation="verify tag reachability; do not re-cut a consumed version",
    )


def _registry_guard(
    bsr_cfg: BsrConfig,
    runtime: RuntimeContext,
    new_version: Version,
) -> tuple[tuple[Blocker, ...], dict[str, Any] | None]:
    """
    Run the registry-collision guard read-only; trips become blocker rows.

    Only called under ``--check-registry``. Returns the extra blockers and the
    registry status row (or ``None`` when no probe ran).
    """
    project_name = runtime.project_metadata.get("name", "") or ""
    try:
        registry = resolve_registry(bsr_cfg, project_name)
    except Exception as exc:  # noqa: BLE001 - config errors surface as blockers here
        return (
            Blocker(
                code="BAD_REGISTRY_CONFIG",
                message=str(exc),
                remediation="fix [tool.semantic_release.bsr] registry setting",
            ),
        ), None

    if registry == "none":
        return (), None

    result = probe_registry(registry, project_name, str(new_version))
    blockers: tuple[Blocker, ...] = ()
    if result is ProbeResult.EXISTS:
        blockers = (
            Blocker(
                code="REGISTRY_COLLISION",
                message=f"{project_name}@{new_version!s} already exists on {registry}",
                remediation="bump the version or resolve the collision",
            ),
        )
    elif result is ProbeResult.UNKNOWN:
        blockers = (
            Blocker(
                code="REGISTRY_PROBE_UNKNOWN",
                message=(
                    f"could not confirm whether {project_name}@{new_version!s} "
                    f"is free on {registry} (fail-closed)"
                ),
                remediation=(
                    "retry, or set [tool.semantic_release.bsr] registry='none' if intentional"
                ),
            ),
        )
    status = {
        "registry": registry,
        "reachable": result is ProbeResult.FREE,
        "detail": str(result),
    }
    return blockers, status


def _emit_plan(
    doc: Any,
    output_format: str,
    write_path: str | None,
) -> None:
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


def _classify_if_released(
    repo_dir: Path,
    translator: VersionTranslator,
    new_version: Version,
    bsr_cfg: BsrConfig,
    bump_stats: BumpStats | None,
) -> tuple[ReleaseDecision | None, tuple[Blocker, ...]]:
    """Decide what a plain `version` dispatch would do at this state."""
    with Repo(str(repo_dir)) as git_repo:
        previously_released_versions = {
            v for _, v in tags_and_versions(list(git_repo.tags), translator)
        }

    if new_version not in previously_released_versions:
        return None, ()

    is_orphaned = bool(is_orphaned_recompute(repo_dir, translator, new_version))
    decision = classify_no_release(
        is_orphaned=is_orphaned,
        bump_stats=bump_stats,
    )
    blocker = (
        (_no_release_blocker(new_version),)
        if bsr_cfg.guard_orphan_tag and is_orphaned
        else ()
    )
    return decision, blocker


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
    translator = runtime.version_translator
    parser = runtime.commit_parser
    opts = runtime.global_cli_options
    repo_dir: Path = runtime.repo_dir

    _bsr_cfg = load_bsr_config(opts.config_file)
    _bsr_path_filter = make_path_filter(_bsr_cfg, os.getcwd())

    _plan_state: dict[str, Any] = {
        "released": False,
        "version": None,
        "tag": None,
        "previous_version": None,
        "decision": None,
        "bump_stats": None,
        "components": (),
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

    prerelease = is_forced_prerelease(
        as_prerelease=False, forced_level_bump=None, prerelease=runtime.prerelease
    )

    if (
        last_release := last_released(config.repo_dir, tag_format=config.tag_format)
    ) is not None:
        _plan_state["previous_version"] = str(last_release[1])

    # Read-only next-version computation: the SAME algorithm and inputs the
    # version command uses on its non-forced path, minus every persistence
    # step that follows it there.
    _bump_stats_box: list[BumpStats] = []

    def _stash_bump_stats(
        level_bump: LevelBump,
        commit_count: int,
        latest_version: Version,
        type_counts: Mapping[str, int],
    ) -> None:
        _bump_stats_box.append(
            BumpStats(
                level_bump=level_bump,
                commit_count=commit_count,
                latest_version=latest_version,
                type_counts=type_counts,
            )
        )

    new_version = next_version(
        repo=Repo(str(repo_dir)),
        translator=translator,
        commit_parser=parser,
        prerelease=prerelease,
        major_on_zero=runtime.major_on_zero,
        allow_zero_version=runtime.allow_zero_version,
        commit_path_filter=_bsr_path_filter,
        bump_stats_sink=_stash_bump_stats,
    )

    _plan_state["version"] = str(new_version)
    _plan_state["tag"] = new_version.as_tag()
    if _bump_stats_box:
        _plan_state["bump_stats"] = _bump_stats_box[0]

    # Per-component report (report-only in `version`; primary here).
    if _bsr_cfg.summary:
        _components = resolve_components(
            _bsr_cfg,
            default_name=runtime.project_metadata.get("name", "") or "(repo)",
        )
        with Repo(str(repo_dir)) as summary_repo:
            _plan_state["components"] = build_summary(
                _components,
                repo=summary_repo,
                translator=translator,
                commit_parser=parser,
                prerelease=prerelease,
                major_on_zero=runtime.major_on_zero,
                allow_zero_version=runtime.allow_zero_version,
                component_path_map=_bsr_cfg.component_path_map,
            )

    # Already-released? Same classification as `version`, recorded as a
    # decision instead of an early exit.
    decision, orphan_blockers = _classify_if_released(
        repo_dir,
        translator,
        new_version,
        _bsr_cfg,
        _bump_stats_box[0] if _bump_stats_box else None,
    )
    _plan_state["decision"] = decision
    _plan_state["blockers"] = orphan_blockers

    # Release-safety guards, read-only: trip => blocker rows, not an abort.
    # The registry probe is OPT-IN (--check-registry): a live registry probe is
    # network-nondeterministic (transient UNKNOWN flips `--strict` between
    # exit 1 and 0), so the default plan is fully offline. `version` still
    # enforces the guard live right before any persistence.
    if check_registry and _bsr_cfg.guard_registry_collision:
        registry_blockers, registry_status = _registry_guard(
            _bsr_cfg, runtime, new_version
        )
        _plan_state["blockers"] = (*_plan_state["blockers"], *registry_blockers)
        _plan_state["registry"] = registry_status

    if _plan_state["decision"] is None:
        _plan_state["released"] = True

    blocked = bool(_plan_state["blockers"]) or (
        _plan_state["decision"] is not None
        and _plan_state["decision"].reason not in _BENIGN_NO_RELEASE_REASONS
    )
    if strict and blocked:
        ctx.exit(1)
