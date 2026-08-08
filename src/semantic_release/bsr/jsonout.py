"""
better-semantic-release additions (bsr): machine-readable output (`--format json`).

The release decision is already structured -- `bsr.explain` produces
ReleaseDecision and BumpStats, `bsr.summary` produces ComponentPlan -- but it
only ever leaves the process as English prose on stderr. Repositories drive this
tool from CI; an agent driving one of them should not have to parse that prose.
This module is the JSON exit for data that already exists. (`resolve_dist_assets`
is the one exception, and says why in its own docstring.)

Field names follow `cli.github_actions_output.VersionGitHubActionsOutput` so the
CLI surface and the Actions surface use one vocabulary. Both documents -- the one
`version` emits and the one `publish` emits -- are built here rather than inline
at their call sites, so they share a single `SCHEMA_VERSION` and cannot drift
apart into two dialects.

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

import glob
import json
from pathlib import Path
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


def resolve_dist_assets(dist_glob_patterns: Sequence[str]) -> list[str]:
    """
    The distribution files `publish` matched, as POSIX-style relative paths.

    Unlike everything else in this module, these names are derived rather than
    read back: `RemoteHvcsBase.upload_dists` reports how *many* files it uploaded,
    not which ones. The expansion here is deliberately the same one it performs --
    `glob.glob(..., recursive=True)` kept to real files, resolved against the
    working directory both run from -- and lives next to the field it feeds so an
    upstream change to that expansion surfaces in one place instead of drifting
    quietly.

    Sorted and POSIX-separated because glob order is filesystem-dependent and the
    document is read by machines that should not see a run-to-run or
    platform-to-platform difference.
    """
    return sorted(
        Path(match).as_posix()
        for pattern in dist_glob_patterns
        for match in glob.glob(pattern, recursive=True)  # noqa: PTH207
        if Path(match).is_file()
    )


def build_publish_document(
    *,
    published: bool,
    tag: str | None,
    assets: Sequence[str],
) -> dict[str, Any]:
    """Assemble the `publish` command's JSON document."""
    return {
        "schema_version": SCHEMA_VERSION,
        "published": published,
        "tag": tag,
        "assets": list(assets),
    }


def emit(document: dict[str, Any]) -> None:
    """Write the document to stdout as one line-terminated JSON object."""
    click.echo(json.dumps(document, indent=2, sort_keys=False))
