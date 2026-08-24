from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from semantic_release.bsr.component_map import parse_component_path_map
from semantic_release.cli.util import load_raw_config_file
from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    import os
    from collections.abc import Mapping

    from semantic_release.bsr.component_map import ComponentPathMap


@dataclass(frozen=True)
class BsrComponent:
    """One monorepo component for the `bsr.summary` release-plan report (C3)."""

    name: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class BsrConfig:
    schema_version: int = 1
    guard_orphan_tag: bool = True
    guard_registry_collision: bool = True
    registry: str = ""  # "", "pypi", "npm", or "none"
    path_filter: bool = False
    paths: tuple[str, ...] = ()
    explain: bool = False
    actionable_errors: bool = False
    summary: bool = False
    components: tuple[BsrComponent, ...] = ()
    component_path_map: ComponentPathMap | None = None
    stable_notes_aggregate: bool = False
    stable_notes_scope: str = "line"  # "line" or "since_stable"


def _parse_components(raw_components: object) -> tuple[BsrComponent, ...]:
    if not isinstance(raw_components, list):
        raise TypeError("components must be an array")
    components: list[BsrComponent] = []
    seen: set[str] = set()
    for index, raw_component in enumerate(raw_components):
        if not isinstance(raw_component, dict):
            raise TypeError(f"components[{index}] must be a table")
        unknown = set(raw_component) - {"name", "paths"}
        if unknown:
            raise ValueError(
                f"components[{index}] has unknown fields: {', '.join(sorted(unknown))}"
            )
        name = raw_component.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"components[{index}].name must be a non-empty string")
        if name in seen:
            raise ValueError(f"duplicate component id: {name}")
        raw_paths = raw_component.get("paths", [])
        if not isinstance(raw_paths, list) or not all(
            isinstance(path, str) and path.strip() for path in raw_paths
        ):
            raise ValueError(f"components[{index}].paths must be an array of strings")
        seen.add(name)
        components.append(BsrComponent(name=name, paths=tuple(raw_paths)))
    return tuple(components)


_BSR_BOOL_FIELDS = (
    "guard_orphan_tag",
    "guard_registry_collision",
    "path_filter",
    "explain",
    "actionable_errors",
    "summary",
    "stable_notes_aggregate",
)
_BSR_FIELDS = {
    "schema_version",
    *_BSR_BOOL_FIELDS,
    "registry",
    "paths",
    "components",
    "component_path_map",
    "stable_notes_scope",
}


def _validate_bsr_table(bsr: Mapping[str, object], config_path: Path) -> int:
    unknown = set(bsr) - _BSR_FIELDS
    if unknown:
        raise InvalidConfiguration(
            f"{config_path}: unknown [tool.semantic_release.bsr] fields: "
            + ", ".join(sorted(unknown))
        )
    schema_version = bsr.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise InvalidConfiguration(
            f"{config_path}: [tool.semantic_release.bsr].schema_version must be 1"
        )
    for field in _BSR_BOOL_FIELDS:
        if field in bsr and not isinstance(bsr[field], bool):
            raise InvalidConfiguration(
                f"{config_path}: [tool.semantic_release.bsr].{field} must be boolean"
            )
    if "registry" in bsr and not isinstance(bsr["registry"], str):
        raise InvalidConfiguration(
            f"{config_path}: [tool.semantic_release.bsr].registry must be a string"
        )
    if "paths" in bsr and (
        not isinstance(bsr["paths"], list)
        or not all(isinstance(path, str) and path.strip() for path in bsr["paths"])
    ):
        raise InvalidConfiguration(
            f"{config_path}: [tool.semantic_release.bsr].paths must be an array of strings"
        )
    if "stable_notes_scope" in bsr and (
        not isinstance(bsr["stable_notes_scope"], str)
        or bsr["stable_notes_scope"] not in {"line", "since_stable"}
    ):
        raise InvalidConfiguration(
            f"{config_path}: stable_notes_scope must be 'line' or 'since_stable'"
        )
    try:
        _parse_components(bsr.get("components", []))
    except (TypeError, ValueError) as exc:
        raise InvalidConfiguration(f"{config_path}: {exc}") from exc
    return schema_version


def load_bsr_config(config_file: str | os.PathLike[str]) -> BsrConfig:
    """
    Read [tool.semantic_release.bsr] out of the config file.

    Missing files and absent BSR tables retain upstream-compatible defaults.
    Explicit BSR tables and component-path-map tables fail closed.
    """
    config_path = Path(config_file)
    try:
        # Reuse PSR's parser: returns the [tool.semantic_release] dict.
        sr = load_raw_config_file(config_path)
    except FileNotFoundError:
        return BsrConfig()
    except InvalidConfiguration as exc:
        raise InvalidConfiguration(f"{config_path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise InvalidConfiguration(f"{config_path}: {exc}") from exc

    if not isinstance(sr, dict):
        raise InvalidConfiguration(f"{config_path}: configuration must be a table")
    bsr = sr.get("bsr")
    if bsr is None:
        return BsrConfig()
    if not isinstance(bsr, dict):
        raise InvalidConfiguration(
            f"{config_path}: [tool.semantic_release.bsr] must be a table"
        )

    schema_version = _validate_bsr_table(bsr, config_path)
    component_path_map = None
    if "component_path_map" in bsr:
        try:
            component_path_map = parse_component_path_map(bsr["component_path_map"])
        except (TypeError, ValueError) as exc:
            raise InvalidConfiguration(
                f"{config_path}: invalid [tool.semantic_release.bsr.component_path_map]: {exc}"
            ) from exc

    try:
        components = _parse_components(bsr.get("components", []))
    except (TypeError, ValueError) as exc:
        raise InvalidConfiguration(f"{config_path}: {exc}") from exc

    return BsrConfig(
        schema_version=schema_version,
        guard_orphan_tag=bsr.get("guard_orphan_tag", True),
        guard_registry_collision=bsr.get("guard_registry_collision", True),
        registry=bsr.get("registry", ""),
        path_filter=bsr.get("path_filter", False),
        paths=tuple(bsr.get("paths", [])),
        explain=bsr.get("explain", False),
        actionable_errors=bsr.get("actionable_errors", False),
        summary=bsr.get("summary", False),
        components=components,
        component_path_map=component_path_map,
        stable_notes_aggregate=bsr.get("stable_notes_aggregate", False),
        stable_notes_scope=bsr.get("stable_notes_scope", "line"),
    )
