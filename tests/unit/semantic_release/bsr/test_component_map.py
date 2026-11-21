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


def _component(component_id: str, root: str, **overrides: object) -> dict[str, object]:
    component: dict[str, object] = {
        "id": component_id,
        "roots": [root],
        "release_paths": [f"{root}/pyproject.toml"],
        "config_path": f"{root}/pyproject.toml",
    }
    component.update(overrides)
    return component


@pytest.mark.parametrize(
    "bad_manifest,error,message",
    [
        pytest.param(
            ["schema_version", 1],
            TypeError,
            "component_path_map must be a table",
            id="not-a-table",
        ),
        pytest.param(
            _manifest(owners=["team"]),
            ValueError,
            "component_path_map has unknown fields: owners",
            id="unknown-top-level-field",
        ),
        pytest.param(
            _manifest(shared_policy=""),
            ValueError,
            "component_path_map.shared_policy must be a non-empty string",
            id="blank-policy",
        ),
        pytest.param(
            _manifest(root_policy="some"),
            ValueError,
            "component path policies must be 'all' or 'none'",
            id="unknown-policy",
        ),
        pytest.param(
            _manifest(components="api"),
            TypeError,
            "components must be an array",
            id="components-not-array",
        ),
        pytest.param(
            _manifest(components=[], rules=[]),
            ValueError,
            "component_path_map.components must not be empty",
            id="no-components",
        ),
        pytest.param(
            _manifest(components=[_component("api", "apps/api", owner="x")]),
            ValueError,
            r"components\[0\] has unknown fields: owner",
            id="component-unknown-field",
        ),
        pytest.param(
            _manifest(components=[_component("apps/api", "apps/api")]),
            ValueError,
            r"components\[0\]\.id must be a stable component identifier",
            id="component-id-with-slash",
        ),
        pytest.param(
            _manifest(components=[_component("api", "/srv/api")]),
            ValueError,
            r"components\[0\]\.roots must be repository-relative",
            id="absolute-root",
        ),
        pytest.param(
            _manifest(components=[_component("api", "C:/api")]),
            ValueError,
            r"components\[0\]\.roots must be repository-relative",
            id="drive-letter-root",
        ),
        pytest.param(
            _manifest(components=[_component("api", "./.")]),
            ValueError,
            r"components\[0\]\.roots must not be empty or repository root",
            id="root-is-repository-root",
        ),
        pytest.param(
            _manifest(
                components=[
                    _component("api", "apps/api", roots=["apps/api", "./apps/api"])
                ]
            ),
            ValueError,
            r"components\[0\]\.roots must not contain duplicate paths",
            id="duplicate-roots",
        ),
        pytest.param(
            _manifest(
                components=[
                    _component("api", "apps/api"),
                    _component("api", "apps/web"),
                ]
            ),
            ValueError,
            "component_path_map component ids must be unique",
            id="duplicate-component-ids",
        ),
        pytest.param(
            _manifest(
                components=[
                    _component("api", "apps"),
                    _component("web", "apps/web"),
                ]
            ),
            ValueError,
            "component roots overlap ambiguously",
            id="overlapping-roots",
        ),
        pytest.param(
            _manifest(
                rules=[
                    {
                        "kind": "component",
                        "path": "apps/api",
                        "components": ["api"],
                        "weight": 1,
                    }
                ]
            ),
            ValueError,
            r"rules\[0\] has unknown fields: weight",
            id="rule-unknown-field",
        ),
        pytest.param(
            _manifest(rules=[{"kind": "shared", "path": "libs", "components": []}]),
            ValueError,
            r"rules\[0\]\.components must not be empty for shared rules",
            id="shared-rule-without-components",
        ),
        pytest.param(
            _manifest(
                rules=[{"kind": "component", "path": "apps/api", "components": ["cli"]}]
            ),
            ValueError,
            r"rules\[0\]\.components reference unknown ids: cli",
            id="rule-unknown-component",
        ),
        pytest.param(
            _manifest(
                rules=[
                    {
                        "kind": "component",
                        "path": "apps/api",
                        "components": ["api", "web"],
                    }
                ]
            ),
            ValueError,
            r"rules\[0\]\.components must contain exactly one id",
            id="component-rule-with-two-ids",
        ),
        pytest.param(
            _manifest(
                rules=[
                    {"kind": "shared", "path": "libs", "components": ["api"]},
                    {"kind": "shared", "path": "./libs", "components": ["web"]},
                ]
            ),
            ValueError,
            "component_path_map rules must be unique by kind and path",
            id="duplicate-rules",
        ),
    ],
)
def test_component_map_validation_names_the_offending_field(
    bad_manifest: object, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        parse_component_path_map(bad_manifest)


def test_component_roots_normalize_separators_and_dot_segments() -> None:
    manifest = parse_component_path_map(
        _manifest(
            components=[
                _component("api", "apps/api", roots=[".\\apps\\.\\api\\"]),
                _component("web", "apps/web"),
            ]
        )
    )

    assert manifest.components[0].roots == ("apps/api",)


def test_shared_policy_all_maps_unruled_nested_paths_to_every_component() -> None:
    manifest = parse_component_path_map(_manifest(shared_policy="all", rules=[]))

    assert map_paths(manifest, ["docs/architecture.rst"]) == ("api", "web")
