"""Unit tests for notes modes and fragment discovery (Task 7)."""

from __future__ import annotations

import pytest

from semantic_release.bsr.fragments import collect_fragments, render_fragments
from semantic_release.bsr.notes import (
    MODE_COMMITS,
    NotesError,
    collect_manual_notes,
    parse_notes_config,
    requires_manual_notes,
)
from semantic_release.errors import InvalidConfiguration


# ---------------------------------------------------------------- fragments
def test_collect_fragments_sorted_and_filtered(tmp_path) -> None:
    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "b.rst").write_text("second", encoding="utf-8")
    (changes / "a.md").write_text("first", encoding="utf-8")
    (changes / ".hidden.md").write_text("nope", encoding="utf-8")
    (changes / "sub").mkdir()
    (changes / "sub" / "nested.md").write_text("nope", encoding="utf-8")
    (changes / "data.json").write_text("{}", encoding="utf-8")

    fragments = collect_fragments(changes)
    assert [fragment.path.name for fragment in fragments] == ["a.md", "b.rst"]
    assert render_fragments(fragments) == "first\n\nsecond"


def test_collect_fragments_missing_dir_is_empty(tmp_path) -> None:
    assert collect_fragments(tmp_path / "nope") == ()


# ------------------------------------------------------------------ config
def test_parse_notes_defaults() -> None:
    cfg = parse_notes_config(None)
    assert cfg.mode == MODE_COMMITS
    assert cfg.require_manual_notes == ""


def test_parse_notes_unknown_mode_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="mode must be one of"):
        parse_notes_config({"mode": "towncrier"})


def test_parse_notes_command_mode_requires_command() -> None:
    with pytest.raises(InvalidConfiguration, match="needs bsr.notes.command"):
        parse_notes_config({"mode": "command"})


def test_parse_notes_bad_require_value_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="require_manual_notes"):
        parse_notes_config({"require_manual_notes": "sometimes"})


def test_config_loads_notes_table(tmp_path) -> None:
    from semantic_release.bsr.config import load_bsr_config

    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[tool.semantic_release.bsr.notes]\n"
        'mode = "hybrid"\n'
        'fragments_dir = "news"\n'
        'require_manual_notes = "major"\n',
        encoding="utf-8",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.notes is not None
    assert cfg.notes.mode == "hybrid"
    assert cfg.notes.fragments_dir == "news"
    assert cfg.notes.require_manual_notes == "major"


def test_config_invalid_notes_fails_closed(tmp_path) -> None:
    from semantic_release.bsr.config import load_bsr_config

    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[tool.semantic_release.bsr.notes]\n"
        'mode = "command"\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidConfiguration, match="bsr.notes"):
        load_bsr_config(cfg_file)


# -------------------------------------------------- require_manual_notes
def test_requires_manual_notes_matrix() -> None:
    cfg_major = parse_notes_config({"require_manual_notes": "major"})
    assert requires_manual_notes(cfg_major, next_bump_is_major=True) is True
    assert requires_manual_notes(cfg_major, next_bump_is_major=False) is False
    cfg_always = parse_notes_config({"require_manual_notes": "always"})
    assert requires_manual_notes(cfg_always, next_bump_is_major=False) is True
    cfg_off = parse_notes_config({})
    assert requires_manual_notes(cfg_off, next_bump_is_major=True) is False


# ----------------------------------------------------- collect_manual_notes
def test_fragments_mode_without_fragments_fails_closed(tmp_path) -> None:
    cfg = parse_notes_config({"mode": "fragments"})
    with pytest.raises(NotesError, match="produced no release notes"):
        collect_manual_notes(cfg, repo_dir=tmp_path)


def test_fragments_mode_renders_found_fragments(tmp_path) -> None:
    changes = tmp_path / "changes"
    changes.mkdir()
    (changes / "fix.md").write_text("fixed the thing", encoding="utf-8")
    cfg = parse_notes_config({"mode": "fragments"})
    text, fragments = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert text == "fixed the thing"
    assert len(fragments) == 1


def test_hybrid_mode_missing_fragments_is_not_fatal(tmp_path) -> None:
    cfg = parse_notes_config({"mode": "hybrid"})
    text, fragments = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert text == ""
    assert fragments == ()


def test_command_mode_captures_stdout(tmp_path) -> None:
    cfg = parse_notes_config(
        {
            "mode": "command",
            "command": ["python", "-c", "print('cmd notes')"],
        }
    )
    text, _ = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert text == "cmd notes"


def test_command_mode_failure_fails_closed(tmp_path) -> None:
    cfg = parse_notes_config(
        {
            "mode": "command",
            "command": ["python", "-c", "import sys; sys.exit(3)"],
        }
    )
    with pytest.raises(NotesError, match="exit 3"):
        collect_manual_notes(cfg, repo_dir=tmp_path)


def test_command_mode_empty_output_fails_closed(tmp_path) -> None:
    cfg = parse_notes_config(
        {"mode": "command", "command": ["python", "-c", "print()"]}
    )
    with pytest.raises(NotesError, match="produced no release notes"):
        collect_manual_notes(cfg, repo_dir=tmp_path)


def test_command_mode_empty_output_allowed(tmp_path) -> None:
    cfg = parse_notes_config(
        {
            "mode": "command",
            "command": ["python", "-c", "print()"],
            "allow_empty_command_output": True,
        }
    )
    text, _ = collect_manual_notes(cfg, repo_dir=tmp_path)
    assert text == ""
