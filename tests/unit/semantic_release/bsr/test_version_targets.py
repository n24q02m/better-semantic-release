"""Unit tests for version target adapters (Task 4)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from git import Repo

from semantic_release.bsr.version_targets import (
    GitTagVersionTarget,
    JsonVersionTarget,
    TextVersionTarget,
    TomlVersionTarget,
    resolve_version_targets,
)
from semantic_release.version.translator import VersionTranslator
from semantic_release.version.version import Version

if TYPE_CHECKING:
    from pathlib import Path

V = Version.parse


def test_json_target_writes_and_preserves_indent(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{\n  "name": "x",\n  "version": "0.1.0"\n}\n', encoding="utf-8"
    )
    target = JsonVersionTarget(package_json, "version", tmp_path)
    touched = target.apply(V("1.2.3"))
    assert touched == package_json
    raw = package_json.read_text(encoding="utf-8")
    assert json.loads(raw)["version"] == "1.2.3"
    assert raw == '{\n  "name": "x",\n  "version": "1.2.3"\n}\n'
    assert target.touched_files() == [package_json]


def test_json_target_nested_field(tmp_path: Path) -> None:
    package_json = tmp_path / "pkg.json"
    package_json.write_text(
        '{"packages": {"core": {"version": "0.1.0"}}}', encoding="utf-8"
    )
    target = JsonVersionTarget(package_json, "packages.core.version", tmp_path)
    target.apply(V("2.0.0"))
    assert (
        json.loads(package_json.read_text(encoding="utf-8"))["packages"]["core"][
            "version"
        ]
        == "2.0.0"
    )


def test_json_target_noop_does_not_write(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text('{"version": "0.1.0"}', encoding="utf-8")
    target = JsonVersionTarget(package_json, "version", tmp_path)
    assert target.apply(V("1.0.0"), noop=True) == package_json
    assert json.loads(package_json.read_text(encoding="utf-8"))["version"] == "0.1.0"


def test_toml_target_bridge_writes_field(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    target = TomlVersionTarget(pyproject, "project.version", tmp_path)
    touched = target.apply(V("1.2.3"))
    assert touched == pyproject
    assert 'version = "1.2.3"' in pyproject.read_text(encoding="utf-8")


def test_text_target_stamps_pattern(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("app 1.0.0 release\n", encoding="utf-8")
    target = TextVersionTarget(version_file, "app {version} release", tmp_path)
    touched = target.apply(V("2.0.0"))
    assert touched == version_file
    assert version_file.read_text(encoding="utf-8") == "app 2.0.0 release\n"


def test_text_target_fail_closed_on_no_match(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("nothing\n", encoding="utf-8")
    target = TextVersionTarget(version_file, "{version}", tmp_path)
    with pytest.raises(ValueError, match="matched nothing"):
        target.apply(V("2.0.0"))
    assert version_file.read_text(encoding="utf-8") == "nothing\n"


def test_text_target_missing_file_fails(tmp_path: Path) -> None:
    target = TextVersionTarget(tmp_path / "absent.txt", "{version}", tmp_path)
    with pytest.raises(FileNotFoundError):
        target.apply(V("2.0.0"))


def test_git_tag_target_creates_and_refuses_recut(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "demo")
        cw.set_value("user", "email", "demo@example.com")
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    repo.index.add(["f.txt"])
    repo.index.commit("feat: one")

    target = GitTagVersionTarget(tmp_path, VersionTranslator(tag_format="v{version}"))
    assert target.preview(V("1.0.0")) == "would create git tag 'v1.0.0' at HEAD"
    assert target.touched_files() == []
    target.apply(V("1.0.0"))
    assert "v1.0.0" in (tag.name for tag in repo.tags)
    assert target.validate_consistency(V("1.0.0")) is None

    with pytest.raises(ValueError, match="already exists"):
        target.apply(V("1.0.0"))

    (tmp_path / "f.txt").write_text("y", encoding="utf-8")
    repo.index.add(["f.txt"])
    repo.index.commit("feat: two")
    message = target.validate_consistency(V("1.0.0"))
    assert message is not None
    assert "different commit" in message


def test_resolve_explicit_targets(tmp_path: Path) -> None:
    from semantic_release.bsr.config import BsrVersionTargetConfig

    targets = resolve_version_targets(
        repo_dir=tmp_path,
        translator=VersionTranslator(tag_format="v{version}"),
        target_configs=[
            BsrVersionTargetConfig(kind="git-tag"),
            BsrVersionTargetConfig(kind="json", path="package.json", field="version"),
        ],
    )
    assert len(targets) == 2
    assert isinstance(targets[0], GitTagVersionTarget)
    assert isinstance(targets[1], JsonVersionTarget)


def test_resolve_default_bridges_declarations(tmp_path: Path) -> None:
    from semantic_release.version.declarations.file import (
        FileVersionDeclaration,
        VersionStampType,
    )

    version_file = tmp_path / "VERSION"
    version_file.write_text("1.0.0\n", encoding="utf-8")
    declaration = FileVersionDeclaration(
        version_file, stamp_format=VersionStampType.NUMBER_FORMAT
    )
    targets = resolve_version_targets(
        repo_dir=tmp_path,
        translator=VersionTranslator(tag_format="v{version}"),
        declarations=[declaration],
    )
    assert len(targets) == 1
    assert targets[0].touched_files() == [version_file]
