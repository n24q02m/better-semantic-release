"""
Fragment workflow coverage (Task 7): discovery + notes modes end to end.

These are deliberately "e2e-lite": they drive the public entry points
(``collect_fragments`` -> ``collect_manual_notes`` -> rendered notes) over a
real temp-tree fragment drop-box, without spawning the CLI. CLI-level note
rendering is covered by the existing cmd_changelog suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from semantic_release.bsr.fragments import collect_fragments, render_fragments
from semantic_release.bsr.notes import (
    NotesError,
    collect_manual_notes,
    parse_notes_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write_fragments(repo_dir: Path, names_to_content: dict[str, str]) -> None:
    changes = repo_dir / "changes"
    changes.mkdir(exist_ok=True)
    for name, content in names_to_content.items():
        (changes / name).write_text(content, encoding="utf-8")


def test_full_fragment_workflow_hybrid_mode(tmp_path: Path) -> None:
    """Hybrid: fragments present -> they render; missing dir -> no error."""
    _write_fragments(
        tmp_path,
        {"1.fix.md": "pin dependency", "2.feature.md": "add exporter"},
    )
    cfg = parse_notes_config({"mode": "hybrid"})

    text, fragments = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert "pin dependency" in text
    assert "add exporter" in text
    assert len(fragments) == 2

    # A repo that removes the drop-box keeps working.
    for fragment in (tmp_path / "changes").iterdir():
        fragment.unlink()
    text_after, fragments_after = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert text_after == ""
    assert fragments_after == ()


def test_full_fragment_workflow_fragments_mode_requires_content(
    tmp_path: Path,
) -> None:
    """Fragments mode is a gate: no fragments -> the run fails closed."""
    cfg = parse_notes_config({"mode": "fragments"})

    with pytest.raises(NotesError, match="produced no release notes"):
        collect_manual_notes(cfg, repo_dir=tmp_path)

    _write_fragments(tmp_path, {"note.md": "the release story"})
    text, _ = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert text == "the release story"


def test_fragment_rendering_is_stable_and_ordered(tmp_path: Path) -> None:
    """Ordering follows file name, not mtime, so notes are reproducible."""
    _write_fragments(
        tmp_path,
        {"9.security.md": "z-last content", "1.breaking.md": "a-first content"},
    )
    fragments = collect_fragments(tmp_path / "changes")
    names = [fragment.path.name for fragment in fragments]
    assert names == ["1.breaking.md", "9.security.md"]
    rendered = render_fragments(fragments)
    assert rendered.index("a-first content") < rendered.index("z-last content")
