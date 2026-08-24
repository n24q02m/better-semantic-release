from __future__ import annotations

import pytest

from semantic_release.bsr.component_map import (
    ComponentPathMap,
    map_changed_paths,
    map_paths,
    parse_component_path_map,
)


def _manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "shared_policy": "none",
        "root_policy": "none",
        "components": [
            {
                "id": "api",
                "roots": ["apps/api"],
                "release_paths": ["apps/api/pyproject.toml"],
                "config_path": "apps/api/pyproject.toml",
            },
            {
                "id": "web",
                "roots": ["apps/web"],
                "release_paths": ["apps/web/package.json"],
                "config_path": "apps/web/package.json",
            },
        ],
        "rules": [
            {"kind": "component", "path": "apps/api", "components": ["api"]},
            {"kind": "component", "path": "apps/web", "components": ["web"]},
            {
                "kind": "shared",
                "path": "libs/shared",
                "components": ["api", "web"],
            },
            {"kind": "root", "path": "README.md", "components": ["api", "web"]},
        ],
    }
    manifest.update(overrides)
    return manifest


def test_parse_validates_versioned_component_map() -> None:
    result = parse_component_path_map(_manifest())

    assert isinstance(result, ComponentPathMap)
    assert result.schema_version == 1
    assert result.component_ids == ("api", "web")
    assert result.components[0].config_path == "apps/api/pyproject.toml"


def test_mapping_obeys_component_shared_root_precedence() -> None:
    manifest = parse_component_path_map(_manifest())

    assert map_paths(manifest, ["apps/api/src/main.py"]) == ("api",)
    assert map_paths(manifest, ["libs/shared/src/common.py"]) == ("api", "web")
    assert map_paths(manifest, ["README.md"]) == ("api", "web")
    assert map_changed_paths(
        manifest,
        old_paths=["apps/api/src/old.py"],
        new_paths=["apps/web/src/new.py"],
    ) == ("api", "web")


def test_mapping_uses_first_matching_ordered_component_rule() -> None:
    manifest = parse_component_path_map(
        _manifest(
            rules=[
                {"kind": "component", "path": "apps/api", "components": ["api"]},
                {
                    "kind": "component",
                    "path": "apps/api/src",
                    "components": ["web"],
                },
            ]
        )
    )

    assert map_paths(manifest, ["apps/api/src/main.py"]) == ("api",)


def test_unmapped_path_fails_closed() -> None:
    manifest = parse_component_path_map(_manifest())

    with pytest.raises(ValueError, match="unmapped repository path"):
        map_paths(manifest, ["docs/architecture.rst"])


@pytest.mark.parametrize(
    "bad_manifest",
    [
        {"schema_version": 2},
        _manifest(schema_version=0),
        _manifest(
            components=[
                {"id": "api", "roots": ["apps/api"]},
                {"id": "api", "roots": ["apps/web"]},
            ]
        ),
        _manifest(
            components=[
                {"id": "api", "roots": ["apps/api", "apps/api/src"]},
                {"id": "web", "roots": ["apps/web"]},
            ]
        ),
        _manifest(
            rules=[{"kind": "component", "path": "../apps/api", "components": ["api"]}]
        ),
        _manifest(
            rules=[{"kind": "unknown", "path": "apps/api", "components": ["api"]}]
        ),
    ],
)
def test_malformed_component_map_fails_closed(bad_manifest: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_component_path_map(bad_manifest)


def test_root_policy_all_is_explicit() -> None:
    manifest = parse_component_path_map(_manifest(root_policy="all", rules=[]))

    assert map_paths(manifest, ["CHANGELOG.rst"]) == ("api", "web")


def test_root_policy_none_explicitly_emits_no_component() -> None:
    manifest = parse_component_path_map(_manifest(root_policy="none", rules=[]))

    assert map_paths(manifest, ["CHANGELOG.rst"]) == ()
