from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semantic_release.bsr.config import BsrConfig, BsrPublishConfig
    from semantic_release.cli.config import RuntimeContext

import click
from git import Repo
from rich.markup import escape

# BSR-PATCH: machine-readable output (better-semantic-release)
from semantic_release.bsr import jsonout
from semantic_release.bsr.config import load_bsr_config
from semantic_release.cli.util import noop_report, rprint
from semantic_release.errors import AssetUploadError, InvalidConfiguration
from semantic_release.globals import logger
from semantic_release.hvcs.remote_hvcs_base import RemoteHvcsBase
from semantic_release.version.algorithm import tags_and_versions

if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from semantic_release.cli.cli_context import CliContextObj


def publish_distributions(
    tag: str,
    hvcs_client: RemoteHvcsBase,
    dist_glob_patterns: tuple[str, ...],
    noop: bool = False,
) -> None:
    if noop:
        noop_report(
            str.join(
                " ",
                [
                    "would have uploaded files matching any of the globs",
                    str.join(", ", [repr(g) for g in dist_glob_patterns]),
                    "to a remote VCS release, if supported",
                ],
            )
        )
        return

    logger.info("Uploading distributions to release")
    for pattern in dist_glob_patterns:
        hvcs_client.upload_dists(tag=tag, dist_glob=pattern)  # type: ignore[attr-defined]


def _plan_gate_blocks_publish(
    cli_ctx: CliContextObj,
    runtime: RuntimeContext,
    bsr_cfg: BsrConfig,
    plan_path: str,
    tag: str,
) -> bool:
    """
    Opt-in plan-consumption gate (W1.4, spec §4.1C): True => refuse to publish.

    Anchors on the tag rather than ``head_sha``: between planning and
    publishing the `version` step legitimately creates a release commit (and
    the tag), so HEAD equality would false-positive on the standard flow.
    """
    from semantic_release.bsr.plan_drift import (
        PlanSnapshotError,
        load_plan_snapshot,
        publish_plan_drift,
    )
    from semantic_release.bsr.preflight import compute_release_state

    try:
        plan_snapshot = load_plan_snapshot(plan_path)
    except PlanSnapshotError as exc:
        rprint(f":x: [bold red]Plan snapshot rejected: {escape(str(exc))}[/bold red]")
        return True

    release_state = compute_release_state(
        runtime=runtime, config=cli_ctx.raw_config, bsr_cfg=bsr_cfg
    )
    with Repo(str(runtime.repo_dir)) as git_repo:
        try:
            planned_tag_reachable: bool | None = git_repo.is_ancestor(
                git_repo.commit(tag), git_repo.head.commit
            )
        except ValueError:
            planned_tag_reachable = False
    findings = publish_plan_drift(
        plan_snapshot,
        publish_tag=tag,
        current_version=str(release_state.new_version),
        planned_tag_reachable=planned_tag_reachable,
    )
    for finding in findings:
        rprint(
            f":x: [bold red][{finding.code}][/bold red] {escape(finding.message)} "
            f"— *fix:* {escape(finding.remediation)}"
        )
    return bool(findings)


def _run_bsr_publishers(
    cli_ctx: CliContextObj, publish_cfg: BsrPublishConfig, *, tag: str, noop: bool
) -> bool:
    """Run opt-in BSR publisher adapters; return True when any failed."""
    from semantic_release.bsr.publishers import build_publishers, run_publishers
    from semantic_release.bsr.registry import probe_registry

    runtime = cli_ctx.runtime_ctx
    if runtime is None:  # publish command always materializes runtime first
        raise InvalidConfiguration("bsr publish adapters: runtime context unavailable")
    version = runtime.version_translator.from_tag(tag)
    if version is None:
        raise InvalidConfiguration(
            f"bsr publish adapters: tag {tag!r} does not match tag format "
            f"{runtime.version_translator.tag_format!r}"
        )

    probe_result = None
    if publish_cfg.probe is not None:
        pcfg = publish_cfg.probe
        probe_result = probe_registry(
            pcfg.kind,
            str(runtime.project_metadata.get("name", "")),
            str(version),
            tag=tag,
            repo=pcfg.repo,
            url_template=pcfg.url_template,
            registry_url=pcfg.registry_url,
        )
    publishers = build_publishers(publish_cfg.publishers, repo_dir=runtime.repo_dir)
    outcomes = run_publishers(publishers, str(version), probe=probe_result, noop=noop)
    for outcome in outcomes:
        rprint(f":bell: [bsr publish] {outcome.state}: {escape(outcome.detail)}")
    return any(outcome.state in {"failed", "retryable"} for outcome in outcomes)


@click.command(
    short_help="Publish distributions to VCS Releases",
    context_settings={
        "help_option_names": ["-h", "--help"],
    },
)
@click.option(
    "--tag",
    "tag",
    help="The tag associated with the release to publish to",
    default="latest",
)
@click.option(
    "--plan",
    "plan_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help=(
        "Opt-in plan-consumption gate: refuse to publish unless the given "
        "`plan --write` snapshot still matches the repository (planned tag, "
        "current version, tag reachability)."
    ),
)
# BSR-PATCH: machine-readable output (better-semantic-release)
@jsonout.add_format_option
@click.pass_obj
def publish(
    cli_ctx: CliContextObj,
    tag: str,
    plan_path: str | None = None,
    # BSR-PATCH: machine-readable output (better-semantic-release)
    output_format: str = jsonout.FORMAT_TABLE,
) -> None:
    """Build and publish a distribution to a VCS release."""
    ctx = click.get_current_context()

    # BSR-PATCH: machine-readable output (better-semantic-release). Same contract
    # as the version command: under `--format json`, stdout carries exactly one
    # JSON document for every way this command can end. It has four exits -- no
    # tags found, unknown tag, a remote that cannot take artifacts, and an upload
    # error -- so the document is emitted from a close-callback that covers them
    # all from one place. Nothing needs suppressing here the way it did in
    # version.py: this command never wrote to stdout, every line it prints goes
    # to stderr through `rprint`.
    _json_state: dict[str, Any] = {"published": False, "tag": None, "assets": []}

    def _emit_json_document() -> None:
        jsonout.emit(
            jsonout.build_publish_document(
                published=_json_state["published"],
                tag=_json_state["tag"],
                assets=_json_state["assets"],
            )
        )

    if output_format == jsonout.FORMAT_JSON:
        ctx.call_on_close(_emit_json_document)

    runtime = cli_ctx.runtime_ctx
    hvcs_client = runtime.hvcs_client
    translator = runtime.version_translator
    dist_glob_patterns = runtime.dist_glob_patterns
    # BSR-PATCH (universal-release-engine Task 5 / W1.4): loaded once up front
    # -- the plan-consumption gate and the publisher adapters share it.
    bsr_cfg = load_bsr_config(cli_ctx.global_opts.config_file)

    with Repo(str(runtime.repo_dir)) as git_repo:
        repo_tags = git_repo.tags

    if tag == "latest":
        try:
            tag = str(tags_and_versions(repo_tags, translator)[0][0])
        except IndexError:
            rprint(
                str.join(
                    " ",
                    [
                        ":x: [bold red]No tags found with format[/bold red]",
                        escape(repr(translator.tag_format)),
                        "[bold red]couldn't identify latest version[/bold red]",
                    ],
                )
            )
            ctx.exit(1)

    # BSR-PATCH: machine-readable output (better-semantic-release). Set once
    # `latest` has been resolved, so the document reports the tag actually being
    # operated on. The unresolved case never reaches here -- it exits above --
    # and so correctly leaves the field null rather than reporting "latest".
    _json_state["tag"] = tag

    if tag not in {tag.name for tag in repo_tags}:
        rprint(
            f":x: [bold red]Tag '{escape(str(tag))}' not found in local repository![/bold red]"
        )
        ctx.exit(1)

    # BSR-PATCH (W1.4, spec §4.1C): opt-in plan-consumption gate. Refuse to
    # publish when the snapshot no longer matches the repository: wrong tag,
    # recomputed version moved, or the planned tag is not reachable from HEAD.
    # Runs before any upload decision; nothing is published on drift.
    if plan_path is not None and _plan_gate_blocks_publish(
        cli_ctx, runtime, bsr_cfg, plan_path, tag
    ):
        ctx.exit(1)

    if not isinstance(hvcs_client, RemoteHvcsBase):
        rprint(
            ":warning: [bold yellow]Remote does not support artifact upload. Exiting with no action taken...[/bold yellow]"
        )
        return

    # BSR-PATCH: machine-readable output (better-semantic-release)
    _json_state["assets"] = jsonout.resolve_dist_assets(dist_glob_patterns)

    try:
        publish_distributions(
            tag=tag,
            hvcs_client=hvcs_client,
            dist_glob_patterns=dist_glob_patterns,
            noop=runtime.global_cli_options.noop,
        )
        # BSR-PATCH: machine-readable output (better-semantic-release). True means
        # the upload step ran to completion, which under `--noop` is the simulated
        # run -- the same meaning `released` carries in the version document.
        _json_state["published"] = True
    except AssetUploadError as err:
        rprint(f":x: [bold red]{escape(str(err))}[/bold red]")
        ctx.exit(1)

    # BSR-PATCH (universal-release-engine Task 5): opt-in publisher adapters.
    # Only active when [tool.bsr.publish] is configured; existing users and the
    # stock action.yml surface never hit this path.
    if (
        bsr_cfg.publish is not None
        and bsr_cfg.publish.publishers
        and _run_bsr_publishers(
            cli_ctx,
            bsr_cfg.publish,
            tag=tag,
            noop=runtime.global_cli_options.noop,
        )
    ):
        ctx.exit(1)
