"""Unit tests for version source adapters (Task 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from semantic_release.bsr.version_sources import (
    DeclarationVersionSource,
    GitTagVersionSource,
    JsonVersionSource,
    TextVersionSource,
    TomlVersionSource,
    VersionProvenance,
    build_version_pattern_regex,
    resolve_version_sources,
)
from semantic_release.version.version import Version

if TYPE_CHECKING:
    from pathlib import Path

from semantic_release.version.translator import VersionTranslator

_AUTHOR = Actor("demo", "demo@example.com")


@pytest.fixture
def tagged_repo(tmp_path: Path) -> Path:
    repo = Repo.init(tmp_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "demo")
        cw.set_value("user", "email", "demo@example.com")
    (tmp_path / "f.txt").write_text("one", encoding="utf-8")
    repo.index.add(["f.txt"])
    repo.index.commit("feat: first")
    repo.create_tag("v1.2.3")
    (tmp_path / "f.txt").write_text("two", encoding="utf-8")
    repo.index.add(["f.txt"])
    repo.index.commit("feat: second")
    repo.create_tag("v2.0.0-rc.1")
    return tmp_path


def test_git_tag_source_loads_versions(tagged_repo: Path) -> None:
    source = GitTagVersionSource(
        tagged_repo, VersionTranslator(tag_format="v{version}")
    )
    versions = source.load()
    assert {str(v) for v in versions} == {"1.2.3", "2.0.0-rc.1"}
    prov = source.provenance()
    assert (prov.kind, prov.location) == ("git-tag", "git tags")


def test_git_tag_source_empty_repo(tmp_path: Path) -> None:
    Repo.init(tmp_path, initial_branch="main")
    source = GitTagVersionSource(tmp_path, VersionTranslator(tag_format="v{version}"))
    assert source.load() == set()


def test_toml_source_reads_dotted_field(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "3.4.5"\n', encoding="utf-8")
    source = TomlVersionSource(pyproject, "project.version")
    assert {str(v) for v in source.load()} == {"3.4.5"}
    prov = source.provenance()
    assert prov.kind == "toml"
    assert prov.detail == "project.version"


def test_toml_source_missing_field_or_file(tmp_path: Path) -> None:
    empty = TomlVersionSource(tmp_path / "absent.toml", "project.version")
    assert empty.load() == set()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert TomlVersionSource(pyproject, "project.version").load() == set()


def test_json_source_reads_dotted_field(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name": "x", "version": "0.9.0"}', encoding="utf-8")
    source = JsonVersionSource(package_json, "version")
    assert {str(v) for v in source.load()} == {"0.9.0"}


def test_json_source_missing_field(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name": "x"}', encoding="utf-8")
    assert JsonVersionSource(package_json, "version").load() == set()


def test_text_source_matches_version_token(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("app 1.0.0 release\n", encoding="utf-8")
    source = TextVersionSource(version_file, "app {version} release")
    assert {str(v) for v in source.load()} == {"1.0.0"}
    prov = source.provenance()
    assert prov.kind == "text"
    assert prov.detail == "app {version} release"


def test_text_source_no_match(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("nothing here\n", encoding="utf-8")
    assert TextVersionSource(version_file, "{version}").load() == set()


def test_resolve_explicit_sources_over_bridge_default(
    tmp_path: Path, tagged_repo: Path
) -> None:
    (tagged_repo / "package.json").write_text('{"version": "5.5.5"}', encoding="utf-8")
    from semantic_release.bsr.config import BsrVersionSourceConfig

    sources = resolve_version_sources(
        repo_dir=tagged_repo,
        translator=VersionTranslator(tag_format="v{version}"),
        source_configs=[
            BsrVersionSourceConfig(kind="json", path="package.json", field="version")
        ],
    )
    assert len(sources) == 1
    assert {str(v) for v in sources[0].load()} == {"5.5.5"}


def test_resolve_default_bridges_tags_and_declarations(
    tmp_path: Path, tagged_repo: Path
) -> None:
    from semantic_release.version.declarations.enum import VersionStampType
    from semantic_release.version.declarations.pattern import (
        PatternVersionDeclaration,
    )

    version_file = tagged_repo / "VERSION"
    version_file.write_text("1.2.3\n", encoding="utf-8")
    # Mirror upstream: version_variables entries become Pattern declarations
    declaration = PatternVersionDeclaration(
        version_file,
        search_text="(?P<version>[0-9.]+)",
        stamp_format=VersionStampType.NUMBER_FORMAT,
    )
    sources = resolve_version_sources(
        repo_dir=tagged_repo,
        translator=VersionTranslator(tag_format="v{version}"),
        declarations=[declaration],
    )
    assert len(sources) == 2
    assert {str(v) for v in sources[0].load()} == {"1.2.3", "2.0.0-rc.1"}
    assert {str(v) for v in sources[1].load()} == {"1.2.3"}


def test_text_pattern_without_version_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="must contain the literal {version} token"):
        build_version_pattern_regex("version = ")


def test_text_pattern_escapes_regex_literals_around_the_token() -> None:
    regex = build_version_pattern_regex("__version__ = ('{version}')")
    match = regex.fullmatch("__version__ = ('1.2.3')")
    assert match is not None
    assert match.group("version") == "1.2.3"
    assert regex.fullmatch("__version__ = '1.2.3'") is None


@pytest.mark.parametrize(
    "content",
    ["[project]\nversion = 3\n", '[project]\nversion = "  "\n'],
    ids=["non-string", "blank"],
)
def test_toml_source_ignores_non_string_versions(tmp_path: Path, content: str) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(content, encoding="utf-8")
    assert TomlVersionSource(pyproject, "project.version").load() == set()


@pytest.mark.parametrize(
    "content",
    ['{"version": 3}', '{"version": ""}'],
    ids=["non-string", "blank"],
)
def test_json_source_ignores_values_that_are_not_versions(
    tmp_path: Path, content: str
) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(content, encoding="utf-8")
    assert JsonVersionSource(package_json, "version").load() == set()


def test_file_sources_report_nothing_for_missing_files(tmp_path: Path) -> None:
    absent = tmp_path / "absent"
    assert JsonVersionSource(absent, "version").load() == set()
    assert TextVersionSource(absent, "{version}").load() == set()
    assert JsonVersionSource(absent, "tool.version").provenance() == (
        VersionProvenance(kind="json", location=str(absent), detail="tool.version")
    )


def test_declaration_without_a_read_path_reports_no_version(tmp_path: Path) -> None:
    from semantic_release.version.declarations.enum import VersionStampType
    from semantic_release.version.declarations.file import FileVersionDeclaration

    version_file = tmp_path / "VERSION"
    version_file.write_text("1.2.3\n", encoding="utf-8")
    declaration = FileVersionDeclaration(version_file, VersionStampType.NUMBER_FORMAT)
    source = DeclarationVersionSource(declaration, "VERSION file")

    with pytest.warns(DeprecationWarning):
        assert source.load() == set()
    assert source.provenance() == VersionProvenance(
        kind="declaration", location="VERSION file"
    )


def test_declaration_label_falls_back_to_the_class_name(tagged_repo: Path) -> None:
    class InMemoryDeclaration:
        def parse(self) -> set[Version]:
            return {Version.parse("1.2.3")}

    sources = resolve_version_sources(
        repo_dir=tagged_repo,
        translator=VersionTranslator(tag_format="v{version}"),
        declarations=[InMemoryDeclaration()],  # type: ignore[list-item]
    )
    assert sources[1].provenance().location == "InMemoryDeclaration"
    assert {str(v) for v in sources[1].load()} == {"1.2.3"}


def test_resolve_builds_every_explicit_source_kind(tagged_repo: Path) -> None:
    from semantic_release.bsr.config import BsrVersionSourceConfig

    (tagged_repo / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3"\n', encoding="utf-8"
    )
    absolute_version = tagged_repo / "VERSION"
    absolute_version.write_text("__version__ = '1.2.3'\n", encoding="utf-8")

    sources = resolve_version_sources(
        repo_dir=tagged_repo,
        translator=VersionTranslator(tag_format="v{version}"),
        source_configs=[
            BsrVersionSourceConfig(kind="git-tag"),
            BsrVersionSourceConfig(
                kind="toml", path="pyproject.toml", field="project.version"
            ),
            BsrVersionSourceConfig(
                kind="text",
                path=str(absolute_version),
                pattern="__version__ = '{version}'",
            ),
        ],
    )

    assert [source.provenance().kind for source in sources] == [
        "git-tag",
        "toml",
        "text",
    ]
    assert sources[1].provenance().location == str(tagged_repo / "pyproject.toml")
    assert sources[2].provenance().location == str(absolute_version)
    assert {str(v) for v in sources[0].load()} == {"1.2.3", "2.0.0-rc.1"}
    assert {str(v) for v in sources[1].load()} == {"1.2.3"}
    assert {str(v) for v in sources[2].load()} == {"1.2.3"}
