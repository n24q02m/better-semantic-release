"""
Notes sources (Task 7): one abstraction for where release notes come from.

Four opt-in modes, each explicit about bump source, notes source, and what
happens when the extra source is missing:

========  ==============  =====================  ============================
mode      version bump    release notes          missing source
========  ==============  =====================  ============================
commits   commits         commits                n/a (default behavior)
fragments commits         fragments only         blocker (fail closed)
hybrid    commits         fragments + commits    falls back to commits
command   commits         command stdout         blocker unless allow_empty
========  ==============  =====================  ============================

``require_manual_notes`` upgrades any mode: when the next bump is major (or
``always``), the run must produce manual notes (fragments or command output)
or it fails closed with an actionable blocker. This is the "large releases
must be readable" lever -- automation never ships an unreadable major note.

The commit-driven notes machinery itself stays in ``stable_notes`` and the
upstream changelog pipeline; this module is the source selector on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from semantic_release.bsr.fragments import (
    collect_fragments,
    render_fragments,
)
from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    from semantic_release.bsr.fragments import Fragment

NOTES_MODES = ("commits", "fragments", "hybrid", "command")
MODE_COMMITS = "commits"
MODE_FRAGMENTS = "fragments"
MODE_HYBRID = "hybrid"
MODE_COMMAND = "command"

WHEN_MAJOR = "major"
WHEN_ALWAYS = "always"
REQUIRE_WHEN = ("", WHEN_MAJOR, WHEN_ALWAYS)


@dataclass(frozen=True)
class BsrNotesConfig:
    """``[tool.semantic_release.bsr.notes]`` settings."""

    mode: str = MODE_COMMITS
    fragments_dir: str = "changes"
    command: tuple[str, ...] = ()
    allow_empty_command_output: bool = False
    # require manual notes: "" (never), "major", or "always"
    require_manual_notes: str = ""

    @property
    def wants_fragments(self) -> bool:
        return self.mode in (MODE_FRAGMENTS, MODE_HYBRID)


class NotesError(InvalidConfiguration):
    """Manual notes were required but no source produced any."""


def parse_notes_config(raw: object) -> BsrNotesConfig:
    """
    Parse the ``[tool.semantic_release.bsr.notes]`` table, fail closed.

    Accepted keys: ``mode``, ``fragments_dir``, ``command`` (list),
    ``allow_empty_command_output`` (bool), ``require_manual_notes``
    (""/"major"/"always").
    """
    if raw is None:
        return BsrNotesConfig()
    if not isinstance(raw, dict):
        raise InvalidConfiguration("bsr.notes must be a table")
    mode = str(raw.get("mode", MODE_COMMITS))
    if mode not in NOTES_MODES:
        raise InvalidConfiguration(
            f"bsr.notes.mode must be one of: {', '.join(NOTES_MODES)}"
        )
    command_raw = raw.get("command", ())
    if isinstance(command_raw, str):
        command: tuple[str, ...] = (command_raw,)
    elif isinstance(command_raw, (list, tuple)):
        command = tuple(str(item) for item in command_raw)
    else:
        raise InvalidConfiguration("bsr.notes.command must be a list")
    if mode == MODE_COMMAND and not command:
        raise InvalidConfiguration("bsr.notes.mode='command' needs bsr.notes.command")
    require = str(raw.get("require_manual_notes", ""))
    if require not in REQUIRE_WHEN:
        raise InvalidConfiguration(
            "bsr.notes.require_manual_notes must be '', 'major' or 'always'"
        )
    return BsrNotesConfig(
        mode=mode,
        fragments_dir=str(raw.get("fragments_dir", "changes")),
        command=command,
        allow_empty_command_output=bool(raw.get("allow_empty_command_output", False)),
        require_manual_notes=require,
    )


def requires_manual_notes(
    notes_config: BsrNotesConfig, *, next_bump_is_major: bool
) -> bool:
    """True when this run must produce manual notes to proceed."""
    if notes_config.require_manual_notes == WHEN_ALWAYS:
        return True
    if notes_config.require_manual_notes == WHEN_MAJOR:
        return next_bump_is_major
    return False


def collect_manual_notes(
    notes_config: BsrNotesConfig,
    *,
    repo_dir: str | Path,
) -> tuple[str, tuple[Fragment, ...]]:
    """
    Gather manual notes for this run.

    Returns ``(rendered_text, fragments)``. ``rendered_text`` is "" when the
    mode needs no manual notes or nothing was found. Raises :class:`NotesError`
    when the configured mode demands a source that produced nothing -- that is
    fail-closed behavior, never a silent empty section.
    """
    fragments: tuple[Fragment, ...] = ()
    text = ""

    if notes_config.wants_fragments:
        fragments = collect_fragments(Path(repo_dir) / notes_config.fragments_dir)
        text = render_fragments(fragments)

    if notes_config.mode == MODE_COMMAND:
        import subprocess

        # S603 is intentional: the command comes from the repository's own
        # committed bsr config (operator-controlled), not from untrusted input.
        completed = subprocess.run(  # noqa: S603
            notes_config.command,
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise NotesError(
                f"bsr.notes command failed (exit {completed.returncode}): "
                + completed.stderr.strip()
            )
        text = completed.stdout.strip()

    if (
        not text
        and notes_config.mode in (MODE_FRAGMENTS, MODE_COMMAND)
        and not (
            notes_config.mode == MODE_COMMAND
            and notes_config.allow_empty_command_output
        )
    ):
        raise NotesError(
            f"bsr.notes mode {notes_config.mode!r} produced no release notes"
        )
    return text, fragments


__all__ = [
    "MODE_COMMITS",
    "MODE_COMMAND",
    "MODE_FRAGMENTS",
    "MODE_HYBRID",
    "NOTES_MODES",
    "BsrNotesConfig",
    "NotesError",
    "collect_manual_notes",
    "parse_notes_config",
    "requires_manual_notes",
]
