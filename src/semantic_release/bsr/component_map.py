"""Validated component-to-path mapping for monorepo release dry-runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

SCHEMA_VERSION = 1
_POLICIES = frozenset({"all", "none"})
_RULE_KINDS = frozenset({"component", "shared", "root"})


@dataclass(frozen=True)
class ComponentMapping:
    """One release component and its owned/configuration paths."""

    component_id: str
    roots: tuple[str, ...]
    release_paths: tuple[str, ...]
    config_path: str


@dataclass(frozen=True)
class MappingRule:
    """One ordered path rule with an explicit precedence kind."""

    kind: str
    path: str
    components: tuple[str, ...]


@dataclass(frozen=True)
class ComponentPathMap:
    """Validated versioned component-path-map manifest."""

    schema_version: int
    shared_policy: str
    root_policy: str
    components: tuple[ComponentMapping, ...]
    rules: tuple[MappingRule, ...]

    @property
    def component_ids(self) -> tuple[str, ...]:
        return tuple(component.component_id for component in self.components)


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a table")
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_list(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{field} must be an array")
    return tuple(value)


def normalize_repository_path(value: object, field: str) -> str:
    """Normalize one relative repository path and reject unsafe spellings."""
    raw = _require_string(value, field).replace("\\", "/")
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        raise ValueError(f"{field} must be repository-relative")

    segments = raw.split("/")
    parts: list[str] = []
    for segment in segments:
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ValueError(f"{field} must not contain parent traversal")
        parts.append(segment)
    if not parts:
        raise ValueError(f"{field} must not be empty or repository root")
    return "/".join(parts)


def _unique_paths(values: object, field: str) -> tuple[str, ...]:
    paths = tuple(
        normalize_repository_path(item, field) for item in _require_list(values, field)
    )
    if len(set(paths)) != len(paths):
        raise ValueError(f"{field} must not contain duplicate paths")
    return paths


def _parse_component(value: object, index: int) -> ComponentMapping:
    field = f"components[{index}]"
    raw = _require_mapping(value, field)
    unknown = set(raw) - {"id", "roots", "release_paths", "config_path"}
    if unknown:
        raise ValueError(f"{field} has unknown fields: {', '.join(sorted(unknown))}")
    component_id = _require_string(raw.get("id"), f"{field}.id")
    if "/" in component_id or "\\" in component_id:
        raise ValueError(f"{field}.id must be a stable component identifier")
    roots = _unique_paths(raw.get("roots"), f"{field}.roots")
    release_paths = _unique_paths(raw.get("release_paths"), f"{field}.release_paths")
    config_path = normalize_repository_path(
        raw.get("config_path"), f"{field}.config_path"
    )
    return ComponentMapping(component_id, roots, release_paths, config_path)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def _parse_rule(value: object, index: int, component_ids: set[str]) -> MappingRule:
    field = f"rules[{index}]"
    raw = _require_mapping(value, field)
    unknown = set(raw) - {"kind", "path", "components"}
    if unknown:
        raise ValueError(f"{field} has unknown fields: {', '.join(sorted(unknown))}")
    kind = _require_string(raw.get("kind"), f"{field}.kind")
    if kind not in _RULE_KINDS:
        raise ValueError(f"{field}.kind must be one of {sorted(_RULE_KINDS)}")
    path = normalize_repository_path(raw.get("path"), f"{field}.path")
    components = tuple(
        _require_string(item, f"{field}.components")
        for item in _require_list(raw.get("components"), f"{field}.components")
    )
    if kind in {"component", "shared"} and not components:
        raise ValueError(f"{field}.components must not be empty for {kind} rules")
    unknown_components = set(components) - component_ids
    if unknown_components:
        raise ValueError(
            f"{field}.components reference unknown ids: {', '.join(sorted(unknown_components))}"
        )
    if kind == "component" and len(components) != 1:
        raise ValueError(f"{field}.components must contain exactly one id")
    return MappingRule(kind, path, components)


def parse_component_path_map(value: object) -> ComponentPathMap:
    """Validate and parse a raw ``component_path_map`` table."""
    raw = _require_mapping(value, "component_path_map")
    unknown = set(raw) - {
        "schema_version",
        "shared_policy",
        "root_policy",
        "components",
        "rules",
    }
    if unknown:
        raise ValueError(
            "component_path_map has unknown fields: " + ", ".join(sorted(unknown))
        )
    schema_version = raw.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != SCHEMA_VERSION:
        raise ValueError(f"component_path_map.schema_version must be {SCHEMA_VERSION}")
    shared_policy = _require_string(
        raw.get("shared_policy"), "component_path_map.shared_policy"
    )
    root_policy = _require_string(
        raw.get("root_policy"), "component_path_map.root_policy"
    )
    if shared_policy not in _POLICIES or root_policy not in _POLICIES:
        raise ValueError("component path policies must be 'all' or 'none'")

    components = tuple(
        _parse_component(item, index)
        for index, item in enumerate(_require_list(raw.get("components"), "components"))
    )
    if not components:
        raise ValueError("component_path_map.components must not be empty")
    component_ids = tuple(component.component_id for component in components)
    if len(set(component_ids)) != len(component_ids):
        raise ValueError("component_path_map component ids must be unique")

    roots = [
        (component.component_id, root)
        for component in components
        for root in component.roots
    ]
    for index, (left_id, left_root) in enumerate(roots):
        for right_id, right_root in roots[index + 1 :]:
            if left_id != right_id and (
                _under(left_root, right_root) or _under(right_root, left_root)
            ):
                raise ValueError("component roots overlap ambiguously")

    rules = tuple(
        _parse_rule(item, index, set(component_ids))
        for index, item in enumerate(_require_list(raw.get("rules"), "rules"))
    )
    rule_keys = [(rule.kind, rule.path) for rule in rules]
    if len(set(rule_keys)) != len(rule_keys):
        raise ValueError("component_path_map rules must be unique by kind and path")

    return ComponentPathMap(
        schema_version=SCHEMA_VERSION,
        shared_policy=shared_policy,
        root_policy=root_policy,
        components=components,
        rules=rules,
    )


def map_paths(manifest: ComponentPathMap, paths: Iterable[str]) -> tuple[str, ...]:
    """Map normalized changed paths to deterministic component IDs."""
    component_ids = set(manifest.component_ids)
    mapped: set[str] = set()
    for raw_path in paths:
        path = normalize_repository_path(raw_path, "changed path")
        component_rules = [
            rule
            for rule in manifest.rules
            if rule.kind == "component" and _under(path, rule.path)
        ]
        if component_rules:
            mapped.update(component_rules[0].components)
            continue

        shared_rules = [
            rule
            for rule in manifest.rules
            if rule.kind == "shared" and _under(path, rule.path)
        ]
        if shared_rules:
            mapped.update(shared_rules[0].components)
            continue

        root_rules = [
            rule for rule in manifest.rules if rule.kind == "root" and path == rule.path
        ]
        if root_rules:
            mapped.update(
                component for rule in root_rules for component in rule.components
            )
            continue

        if "/" not in path and manifest.root_policy == "all":
            mapped.update(component_ids)
            continue
        if "/" not in path and manifest.root_policy == "none":
            continue
        if manifest.shared_policy == "all":
            mapped.update(component_ids)
            continue
        raise ValueError(f"unmapped repository path: {path}")

    return tuple(sorted(mapped))


def map_changed_paths(
    manifest: ComponentPathMap,
    *,
    old_paths: Iterable[str] = (),
    new_paths: Iterable[str] = (),
) -> tuple[str, ...]:
    """Map the union of old/new paths for deletes, renames, and modifications."""
    return map_paths(manifest, (*old_paths, *new_paths))
