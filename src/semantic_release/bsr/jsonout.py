"""
better-semantic-release additions (bsr): machine-readable output (`--format json`).

The release decision is already structured -- `bsr.explain` produces
ReleaseDecision and BumpStats, `bsr.summary` produces ComponentPlan -- but it
only ever leaves the process as English prose on stderr. Repositories drive this
tool from CI; an agent driving one of them should not have to parse that prose.
This module is the JSON exit for data that already exists.

Field names follow `cli.github_actions_output.VersionGitHubActionsOutput` so the
CLI surface and the Actions surface use one vocabulary.

The contract is that stdout carries exactly one JSON document and nothing else.
Keeping it needs no stream juggling here, because narration is already
stderr-only by construction everywhere upstream: `cli.util.rprint` passes
`file=sys.stderr` (and `rich.print` with an explicit `file` bypasses the global
console entirely), and the CLI's log handler is built as
`RichHandler(console=Console(stderr=True))`. What is left on stdout is the
handful of deliberate `click.echo` data lines, which each command suppresses in
JSON mode. Enforcement lives in the tests, which assert by parsing `stdout`
whole -- if anything else ever lands there, they fail loudly rather than a
redirect quietly papering over it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from typing import Any, Sequence

    from semantic_release.bsr.explain import BumpStats, ReleaseDecision
    from semantic_release.bsr.summary import ComponentPlan

FORMAT_TABLE = "table"
FORMAT_JSON = "json"
SCHEMA_VERSION = 1


def add_format_option(command: Any) -> Any:
    """Register --format on a command, defaulting to the human format."""
    return click.option(
        "--format",
        "output_format",
        type=click.Choice([FORMAT_TABLE, FORMAT_JSON]),
        default=FORMAT_TABLE,
        help="Output format. 'json' prints one machine-readable document on "
        "stdout and moves all human-facing output to stderr.",
    )(command)


def _is_prerelease(version: str | None) -> bool:
    """
    True when `version` carries a SemVer prerelease segment.

    The prerelease is what follows the first `-`, but only within the part
    before any `+`: in `1.0.0+build-7` the hyphen belongs to build metadata and
    the version is a normal release.
    """
    if not version:
        return False
    return "-" in version.split("+", 1)[0]


def build_version_document(
    *,
    released: bool,
    version: str | None,
    tag: str | None,
    previous_version: str | None,
    decision: ReleaseDecision | None,
    bump_stats: BumpStats | None,
    components: Sequence[ComponentPlan],
) -> dict[str, Any]:
    """Assemble the `version` command's JSON document."""
    return {
        "schema_version": SCHEMA_VERSION,
        "released": released,
        "version": version,
        "tag": tag,
        "is_prerelease": _is_prerelease(version),
        "previous_version": previous_version,
        "reason": decision.reason if decision is not None else None,
        "commit_count": (
            decision.commit_count
            if decision is not None
            else (bump_stats.commit_count if bump_stats is not None else 0)
        ),
        "level_bump": (
            bump_stats.level_bump.name.lower() if bump_stats is not None else None
        ),
        "type_counts": dict(bump_stats.type_counts) if bump_stats is not None else {},
        "components": [
            {
                "name": c.name,
                "would_release": c.would_release,
                "level": c.level,
                "commit_count": c.commit_count,
                "sample_paths": list(c.sample_paths),
                "resulting_version": str(c.resulting_version),
            }
            for c in components
        ],
    }


def emit(document: dict[str, Any]) -> None:
    """Write the document to stdout as one line-terminated JSON object."""
    click.echo(json.dumps(document, indent=2, sort_keys=False))
