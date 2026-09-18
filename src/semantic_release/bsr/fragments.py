"""
Newsfragment discovery (Task 7).

Fragments are optional plain-text note snippets a repo drops into a
versioned directory (Towncrier-style, but with no type taxonomy and no
mandatory workflow). Discovery is deliberately dumb: sorted, stable, and
ignoring anything hidden. A repo that never writes fragments keeps the
commit-driven behavior untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FRAGMENT_SUFFIXES = (".md", ".rst", ".txt")


@dataclass(frozen=True)
class Fragment:
    """One discovered newsfragment file."""

    path: Path
    content: str


def collect_fragments(directory: str | Path) -> tuple[Fragment, ...]:
    """
    Return all readable fragment files under ``directory``, sorted by name.

    - Only ``*.md``, ``*.rst``, ``*.txt`` files (case-insensitive).
    - Hidden files/dirs (leading dot) are skipped.
    - Missing directory -> no fragments (fragments are optional everywhere).
    - Subdirectories are not descended into: one flat drop-box.
    """
    root = Path(directory)
    if not root.is_dir():
        return ()
    fragments: list[Fragment] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith("."):
            continue
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in FRAGMENT_SUFFIXES:
            continue
        try:
            content = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fragments.append(Fragment(path=entry, content=content))
    return tuple(fragments)


def render_fragments(fragments: tuple[Fragment, ...]) -> str:
    """Join fragment contents into one notes block, separated by blank lines."""
    return "\n\n".join(
        fragment.content.strip() for fragment in fragments if fragment.content.strip()
    )


__all__ = ["FRAGMENT_SUFFIXES", "Fragment", "collect_fragments", "render_fragments"]
