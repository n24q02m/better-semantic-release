"""Unit tests for commit-message presets (wave 2, W2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from semantic_release.bsr.commit_presets import (
    CommitPresetError,
    commit_subjects_since_latest_tag,
    load_preset,
    parse_subject,
    preset_spec_help,
    validate_subjects,
)

CONVENTIONAL_OK = [
    "feat: add thing",
    "feat(api)!: change contract",
    "fix(parser): handle empty input",
    "chore(deps): bump",
    "docs: update readme",
]
CONVENTIONAL_BAD = [
    "updated some stuff",
    "feat missing colon",
    "Merge branch 'x'",
]


class TestLoadPreset:
    @pytest.mark.parametrize("name", ["conventional", "commitizen", "gitmoji-map"])
    def test_builtins_resolve(self, name: str) -> None:
        preset = load_preset(name)
        assert preset.name == name
        assert preset.mode == "reject"

    def test_unknown_name_lists_known(self) -> None:
        with pytest.raises(CommitPresetError, match="conventional"):
            load_preset("nope")

    @pytest.mark.parametrize("spec", ["conventional:warn", "conventional:parse"])
    def test_mode_override(self, spec: str) -> None:
        preset = load_preset(spec)
        assert preset.mode in {"warn", "parse"}

    def test_mode_override_bad_mode_is_just_a_path(self) -> None:
        with pytest.raises(CommitPresetError, match="unknown commit preset"):
            load_preset("conventional:loud")


class TestConventional:
    def test_conforming_subjects_parse(self) -> None:
        preset = load_preset("conventional")
        for subject in CONVENTIONAL_OK:
            parsed = parse_subject(preset, subject)
            assert parsed.conforming, subject
            assert parsed.type in {"feat", "fix", "chore", "docs"}

    def test_breaking_marker_detected(self) -> None:
        preset = load_preset("conventional")
        parsed = parse_subject(preset, "feat(api)!: change contract")
        assert parsed.breaking is True
        assert parsed.scope == "api"

    def test_non_conforming_detected(self) -> None:
        preset = load_preset("conventional")
        for subject in CONVENTIONAL_BAD:
            assert not parse_subject(preset, subject).conforming, subject

    def test_types_map_normalizes(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack.toml"
        pack.write_text(
            'name = "team"\n'
            "pattern = '^(?P<type>[a-z]+)(\\([^)]*\\))?(?P<breaking>!)?: '\n"
            "[types]\n"
            'feat = "improvement"\n',
            encoding="utf-8",
        )
        preset = load_preset(str(pack))
        parsed = parse_subject(preset, "improvement: faster")
        assert parsed.conforming
        assert parsed.type == "feat"


class TestGitmoji:
    def test_shortcode(self) -> None:
        preset = load_preset("gitmoji-map")
        parsed = parse_subject(preset, ":sparkles: add feature")
        assert parsed.conforming
        assert parsed.type == "feat"
        assert parsed.raw_type == ":sparkles:"

    def test_unicode_glyph(self) -> None:
        preset = load_preset("gitmoji-map")
        parsed = parse_subject(preset, "\u2728 add feature")
        assert parsed.conforming
        assert parsed.type == "feat"

    def test_accented_word_is_not_emoji(self) -> None:
        preset = load_preset("gitmoji-map")
        assert not parse_subject(preset, "naïve fix for caching").conforming

    def test_unknown_emoji_falls_back_to_misc(self) -> None:
        preset = load_preset("gitmoji-map")
        parsed = parse_subject(preset, "\U0001f9d9 wizardry")
        assert parsed.conforming
        assert parsed.type == "misc"

    def test_plain_subject_rejected(self) -> None:
        preset = load_preset("gitmoji-map")
        assert not parse_subject(preset, "fix: conventional-style").conforming


class TestTomlPack:
    def test_custom_pack_loads(self, tmp_path: Path) -> None:
        pack = tmp_path / "team.toml"
        pack.write_text(
            'name = "team-style"\n'
            'mode = "warn"\n'
            "pattern = '^(?P<type>[a-z]+): '\n",
            encoding="utf-8",
        )
        preset = load_preset(str(pack))
        assert preset.name == "team-style"
        assert preset.mode == "warn"

    def test_spec_suffix_overrides_pack_mode(self, tmp_path: Path) -> None:
        pack = tmp_path / "team.toml"
        pack.write_text(
            'name = "t"\npattern = "^(?P<type>[a-z]+): "\n', encoding="utf-8"
        )
        assert load_preset(f"{pack}:reject").mode == "reject"

    def test_missing_name_rejected(self, tmp_path: Path) -> None:
        pack = tmp_path / "bad.toml"
        pack.write_text("pattern = 'x'\n", encoding="utf-8")
        with pytest.raises(CommitPresetError, match="name"):
            load_preset(str(pack))

    def test_bad_pattern_rejected(self, tmp_path: Path) -> None:
        pack = tmp_path / "bad.toml"
        pack.write_text('name = "x"\npattern = "([unclosed"\n', encoding="utf-8")
        with pytest.raises(CommitPresetError, match="does not compile"):
            load_preset(str(pack))

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(CommitPresetError, match="unknown commit preset"):
            load_preset(str(tmp_path / "ghost.toml"))


class TestValidate:
    def test_audit_counts_and_histogram(self) -> None:
        preset = load_preset("conventional")
        subjects = [
            ("a" * 7, "feat: one"),
            ("b" * 7, "feat: two"),
            ("c" * 7, "nope"),
        ]
        audit = validate_subjects(preset, subjects)
        assert audit.total == 3
        assert audit.passed_count == 2
        assert [(v.sha, v.subject) for v in audit.violations] == [("c" * 7, "nope")]
        assert audit.types == {"feat": 2}

    def test_empty_range_passes(self) -> None:
        audit = validate_subjects(load_preset("conventional"), [])
        assert audit.total == 0
        assert audit.passed_count == 0
        assert audit.violations == ()


class TestRepoRange:
    def test_collects_commits_since_tag_skipping_merges(self, tmp_path: Path) -> None:
        import subprocess

        env_g = ["-c", "user.email=t@e.st", "-c", "user.name=T"]

        def run(*args: str) -> None:
            subprocess.run(  # noqa: S603 - fixed argv, tmp_path cwd
                ["git", *env_g, *args],  # noqa: S607 - fixed binary name
                cwd=tmp_path,
                check=True,
                capture_output=True,
            )

        run("init", "-q")
        (tmp_path / "f.txt").write_text("1\n", encoding="utf-8")
        run("add", ".")
        run("commit", "-q", "-m", "feat: before tag")
        run("tag", "v1.0.0")
        (tmp_path / "f.txt").write_text("2\n", encoding="utf-8")
        run("add", ".")
        run("commit", "-q", "-m", "feat: after tag")
        (tmp_path / "f.txt").write_text("3\n", encoding="utf-8")
        run("add", ".")
        run("commit", "-q", "-m", "not conventional at all")

        subjects, base_desc = commit_subjects_since_latest_tag(tmp_path)
        assert base_desc == "tag v1.0.0"
        assert [s for _, s in subjects] == [
            "feat: after tag",
            "not conventional at all",
        ]

        audit = validate_subjects(
            load_preset(
                "conventional",
            ),
            subjects,
        )
        assert audit.passed_count == 1
        assert audit.violations[0].subject == "not conventional at all"


def test_help_mentions_builtins_and_modes() -> None:
    help_text = preset_spec_help()
    for token in ("conventional", "gitmoji-map", "TOML", ":warn"):
        assert token in help_text, token
