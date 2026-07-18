"""
better-semantic-release additions (bsr): actionable error messages
(`bsr.actionable_errors`).

PSR surfaces its top recurring cryptic failures as raw `str(exc)` with no
"what happened / why / how to fix" framing (`cli/cli_context.py`,
`cli/config.py`). `format_actionable()` maps the exception types behind the
most-cited issues to an enriched message; callers fall back to `str(exc)`
when unmapped -- pure message enrichment, no new exception types.
"""

from __future__ import annotations

from pydantic import ValidationError

from semantic_release.errors import MissingGitRemote, ParserLoadError

_PRERELEASE_BUMP_MISMATCH = (
    "Cannot increment a non-prerelease version with a prerelease level bump"
)


def format_actionable(exc: BaseException) -> str | None:
    """
    Map a recurring cryptic PSR failure to a "what/why/fix" message.

    Returns None when `exc` isn't one of the mapped categories -- callers
    fall back to `str(exc)` in that case.
    """
    if isinstance(exc, ValueError) and _PRERELEASE_BUMP_MISMATCH in str(exc):
        return (
            "better-semantic-release actionable error: PRERELEASE BUMP MISMATCH.\n"
            f"  {exc!s}\n"
            "  This happens when a prerelease-level bump is requested (--prerelease, "
            "or --as-prerelease/--prerelease-token) but the base version is NOT "
            "already a prerelease.\n"
            "  Fix: drop --as-prerelease/--prerelease-token/--prerelease, or first cut "
            "an initial prerelease (e.g. --prerelease-token=rc) before requesting "
            "another prerelease revision bump."
        )

    if isinstance(exc, ValidationError):
        lines = ["better-semantic-release actionable error: INVALID CONFIGURATION."]
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            msg = err.get("msg", "")
            lines.append(
                f"  [tool.semantic_release.{loc}]: {msg}" if loc else f"  {msg}"
            )
        lines.append(
            "  Fix: check the listed key(s) under [tool.semantic_release] in your "
            "config file."
        )
        return "\n".join(lines)

    if isinstance(exc, ParserLoadError):
        return (
            "better-semantic-release actionable error: PARSER LOAD FAILED.\n"
            f"  {exc!s}\n"
            "  Fix: [tool.semantic_release] commit_parser must be a known parser name "
            "(angular, conventional, conventional-monorepo, emoji, scipy) or a valid "
            "'module:ClassName' import path to an installed, importable custom parser."
        )

    if isinstance(exc, MissingGitRemote):
        return (
            "better-semantic-release actionable error: GIT REMOTE NOT FOUND.\n"
            f"  {exc!s}\n"
            "  Fix: add the remote (e.g. `git remote add origin <url>`), or set "
            "[tool.semantic_release.remote] name/url to an existing remote."
        )

    return None


def tag_format_sanity_note(
    *, total_tags: int, matched_tags: int, tag_format: str
) -> str | None:
    """
    Diagnostic for the tag_format footgun (#1196): the repo has git tags, but
    none of them matched the configured `tag_format`, so PSR silently treats
    it as having no prior releases and starts over from the default initial
    version. Returns None when there's nothing to warn about (no tags at
    all, or at least one tag matched).
    """
    if total_tags == 0 or matched_tags > 0:
        return None
    return (
        "better-semantic-release actionable note: TAG_FORMAT MISMATCH.\n"
        f"  Found {total_tags} git tag(s) but 0 matched tag_format={tag_format!r}.\n"
        "  PSR will treat this repository as having no prior releases and compute "
        "from the default initial version.\n"
        "  Fix: check [tool.semantic_release] tag_format against your existing tags."
    )
