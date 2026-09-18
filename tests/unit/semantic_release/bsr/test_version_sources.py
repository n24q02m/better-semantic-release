"""Unit tests for version source adapters (Task 4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from semantic_release.bsr.version_sources import (
    GitTagVersionSource,
    JsonVersionSource,
    TextVersionSource,
    TomlVersionSource,
    resolve_version_sources,
)

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
