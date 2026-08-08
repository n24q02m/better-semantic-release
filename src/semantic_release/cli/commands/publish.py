from __future__ import annotations

from typing import TYPE_CHECKING

import click
from git import Repo
from rich.markup import escape

# BSR-PATCH: machine-readable output (better-semantic-release)
from semantic_release.bsr import jsonout
from semantic_release.cli.util import noop_report, rprint
from semantic_release.errors import AssetUploadError
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
# BSR-PATCH: machine-readable output (better-semantic-release)
@jsonout.add_format_option
@click.pass_obj
def publish(
    cli_ctx: CliContextObj,
    tag: str,
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
