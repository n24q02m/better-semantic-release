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
    version: BsrVersionConfig | None = None
    publish: BsrPublishConfig | None = None
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
    "version",
    "publish",
}


@dataclass(frozen=True)
class BsrVersionSourceConfig:
    """One explicit version source from ``[tool.bsr.version.sources]``."""

    kind: str  # "git-tag" | "toml" | "json" | "text"
    path: str = ""  # toml/json/text
    field: str = ""  # toml/json dotted key
    pattern: str = ""  # text, with a literal {version} token


@dataclass(frozen=True)
class BsrVersionTargetConfig:
    """One explicit version target from ``[tool.bsr.version.targets]``."""

    kind: str  # "git-tag" | "toml" | "json" | "text"
    path: str = ""  # toml/json/text
    field: str = ""  # toml/json dotted key
    pattern: str = ""  # text, with a literal {version} token


@dataclass(frozen=True)
class BsrVersionConfig:
    """``[tool.bsr.version]``: explicit source/target adapters (Task 4)."""

    sources: tuple[BsrVersionSourceConfig, ...] = ()
    targets: tuple[BsrVersionTargetConfig, ...] = ()


_SOURCE_KINDS = {"git-tag", "toml", "json", "text"}
_SOURCE_REQUIRED = {
    "git-tag": set(),
    "toml": {"path", "field"},
    "json": {"path", "field"},
    "text": {"path", "pattern"},
}


def _parse_version_entry(
    raw: object, label: str, config_path: Path, is_source: bool
) -> BsrVersionSourceConfig | BsrVersionTargetConfig:
    if not isinstance(raw, dict):
        raise InvalidConfiguration(f"{config_path}: {label} must be a table")
    unknown = set(raw) - {"kind", "path", "field", "pattern"}
    if unknown:
        raise InvalidConfiguration(
            f"{config_path}: {label} has unknown fields: " + ", ".join(sorted(unknown))
        )
    kind = raw.get("kind")
    if kind not in _SOURCE_KINDS:
        raise InvalidConfiguration(
            f"{config_path}: {label}.kind must be one of: "
            + ", ".join(sorted(_SOURCE_KINDS))
        )
    missing = _SOURCE_REQUIRED[kind] - set(raw)
    if missing:
        raise InvalidConfiguration(
            f"{config_path}: {label} (kind {kind!r}) requires "
            + ", ".join(sorted(missing))
        )
    fields = {
        "kind": kind,
        "path": raw.get("path", ""),
        "field": raw.get("field", ""),
        "pattern": raw.get("pattern", ""),
    }
    return BsrVersionSourceConfig(**fields) if is_source else BsrVersionTargetConfig(**fields)


def _parse_version_list(
    raw_list: object, label: str, config_path: Path, is_source: bool
) -> list[BsrVersionSourceConfig] | list[BsrVersionTargetConfig]:
    if not isinstance(raw_list, list):
        raise InvalidConfiguration(
            f"{config_path}: bsr.version.{label} must be an array of tables"
        )
    entries = [
        _parse_version_entry(raw, f"bsr.version.{label}[{index}]", config_path, is_source)
        for index, raw in enumerate(raw_list)
    ]
    if is_source:
        return [entry for entry in entries if isinstance(entry, BsrVersionSourceConfig)]
    return [entry for entry in entries if isinstance(entry, BsrVersionTargetConfig)]


@dataclass(frozen=True)
class BsrPublishProbeConfig:
    """Registry probe for the publish gate (Task 5)."""

    kind: str  # none|pypi|npm|crates|oci|github-release|http
    repo: str = ""  # github-release: "owner/repo"
    url_template: str = ""  # http: template with {name}/{version}
    registry_url: str = ""  # oci: registry host


@dataclass(frozen=True)
class BsrPublishCommandConfig:
    """One publisher step (Task 5)."""

    kind: str  # none|github-release|pypi|npm|crates|oci|shell
    name: str = ""  # display label; defaults to kind
    command: tuple[str, ...] = ()  # explicit argv; presets exist for pypi/npm/crates
    manifest_path: str = ""  # github-release: staged manifest
    workspace: str = ""  # github-release: workspace dir


@dataclass(frozen=True)
class BsrPublishConfig:
    """``[tool.bsr.publish]``: probe + publisher adapters, opt-in."""

    probe: BsrPublishProbeConfig | None = None
    publishers: tuple[BsrPublishCommandConfig, ...] = ()


def _parse_version_tables(version: object, config_path: Path) -> BsrVersionConfig:
    """Parse ``[tool.bsr.version]`` sources/targets; unknown kinds fail closed."""
    if not isinstance(version, dict):
        raise InvalidConfiguration(
            f"{config_path}: [tool.semantic_release.bsr.version] must be a table"
        )
    unknown = set(version) - {"sources", "targets"}
    if unknown:
        raise InvalidConfiguration(
            f"{config_path}: unknown [tool.semantic_release.bsr.version] fields: "
            + ", ".join(sorted(unknown))
        )
    sources = _parse_version_list(version.get("sources", []), "sources", config_path, True)
    targets = _parse_version_list(version.get("targets", []), "targets", config_path, False)
    return BsrVersionConfig(
        sources=tuple(sources),  # type: ignore[arg-type]
        targets=tuple(targets),  # type: ignore[arg-type]
    )


_PUBLISH_PROBE_KINDS = {"none", "pypi", "npm", "crates", "oci", "github-release", "http"}
_PUBLISH_PUBLISHER_KINDS = {"none", "github-release", "pypi", "npm", "crates", "oci", "shell"}


def _parse_publish_tables(publish: object, config_path: Path) -> BsrPublishConfig:
    if not isinstance(publish, dict):
        raise InvalidConfiguration(
            f"{config_path}: [tool.semantic_release.bsr.publish] must be a table"
        )
    unknown = set(publish) - {"probe", "publishers"}
    if unknown:
        raise InvalidConfiguration(
            f"{config_path}: unknown [tool.semantic_release.bsr.publish] fields: "
            + ", ".join(sorted(unknown))
        )

    probe: BsrPublishProbeConfig | None = None
    raw_probe = publish.get("probe")
    if raw_probe is not None:
        if not isinstance(raw_probe, dict):
            raise InvalidConfiguration(
                f"{config_path}: bsr.publish.probe must be a table"
            )
        unknown = set(raw_probe) - {"kind", "repo", "url_template", "registry_url"}
        if unknown:
            raise InvalidConfiguration(
                f"{config_path}: bsr.publish.probe has unknown fields: "
                + ", ".join(sorted(unknown))
            )
        kind = raw_probe.get("kind")
        if kind not in _PUBLISH_PROBE_KINDS:
            raise InvalidConfiguration(
                f"{config_path}: bsr.publish.probe.kind must be one of: "
                + ", ".join(sorted(_PUBLISH_PROBE_KINDS))
            )
        probe = BsrPublishProbeConfig(
            kind=kind,
            repo=raw_probe.get("repo", ""),
            url_template=raw_probe.get("url_template", ""),
            registry_url=raw_probe.get("registry_url", ""),
        )

    publishers = _parse_publisher_entries(publish.get("publishers", []), config_path)
    return BsrPublishConfig(probe=probe, publishers=tuple(publishers))


def _parse_publisher_entries(
    raw_publishers: object, config_path: Path
) -> list[BsrPublishCommandConfig]:
    if not isinstance(raw_publishers, list):
        raise InvalidConfiguration(
            f"{config_path}: bsr.publish.publishers must be an array of tables"
        )
    publishers: list[BsrPublishCommandConfig] = []
    for index, raw in enumerate(raw_publishers):
        label = f"bsr.publish.publishers[{index}]"
        if not isinstance(raw, dict):
            raise InvalidConfiguration(f"{config_path}: {label} must be a table")
        unknown = set(raw) - {"kind", "name", "command", "manifest_path", "workspace"}
        if unknown:
            raise InvalidConfiguration(
                f"{config_path}: {label} has unknown fields: " + ", ".join(sorted(unknown))
            )
        kind = raw.get("kind")
        if kind not in _PUBLISH_PUBLISHER_KINDS:
            raise InvalidConfiguration(
                f"{config_path}: {label}.kind must be one of: "
                + ", ".join(sorted(_PUBLISH_PUBLISHER_KINDS))
            )
        raw_command = raw.get("command", [])
        if not isinstance(raw_command, list) or not all(
            isinstance(part, str) for part in raw_command
        ):
            raise InvalidConfiguration(
                f"{config_path}: {label}.command must be an array of strings"
            )
        publishers.append(
            BsrPublishCommandConfig(
                kind=kind,
                name=raw.get("name", ""),
                command=tuple(raw_command),
                manifest_path=raw.get("manifest_path", ""),
                workspace=raw.get("workspace", ""),
            )
        )
    return publishers


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

    version_cfg = (
        _parse_version_tables(bsr["version"], config_path)
        if "version" in bsr
        else None
    )
    publish_cfg = (
        _parse_publish_tables(bsr["publish"], config_path)
        if "publish" in bsr
        else None
    )

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
        version=version_cfg,
        publish=publish_cfg,
        stable_notes_aggregate=bsr.get("stable_notes_aggregate", False),
        stable_notes_scope=bsr.get("stable_notes_scope", "line"),
    )
