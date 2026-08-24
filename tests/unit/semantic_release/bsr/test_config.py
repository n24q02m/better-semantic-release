from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from semantic_release.bsr.component_map import ComponentPathMap
from semantic_release.bsr.config import BsrComponent, BsrConfig, load_bsr_config
from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    from pathlib import Path


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_defaults_when_no_bsr_table(tmp_path: Path):
    cfg_file = _write(tmp_path, '[tool.semantic_release]\ntag_format = "v{version}"\n')
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(
        guard_orphan_tag=True,
        guard_registry_collision=True,
        registry="",
        path_filter=False,
        paths=(),
        explain=False,
        actionable_errors=False,
    )


def test_reads_bsr_table(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n"
        "guard_registry_collision = true\n"
        'registry = "npm"\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(
        guard_orphan_tag=False,
        guard_registry_collision=True,
        registry="npm",
        path_filter=False,
        paths=(),
    )


def test_reads_path_filter_and_paths(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "path_filter = true\n"
        'paths = ["apps/api", "libs/x"]\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(path_filter=True, paths=("apps/api", "libs/x"))


def test_path_filter_and_paths_default_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.path_filter is False
    assert cfg.paths == ()


def test_reads_explain_and_actionable_errors(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "explain = true\n"
        "actionable_errors = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.explain is True
    assert cfg.actionable_errors is True


def test_explain_and_actionable_errors_default_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.explain is False
    assert cfg.actionable_errors is False


def test_missing_file_returns_defaults(tmp_path: Path):
    assert load_bsr_config(tmp_path / "nope.toml") == BsrConfig()


def test_malformed_config_fails_closed(tmp_path: Path):
    cfg_file = _write(tmp_path, "this is not valid toml :::\n")

    with pytest.raises(InvalidConfiguration):
        load_bsr_config(cfg_file)


def test_reads_summary_flag(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "summary = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.summary is True


def test_summary_defaults_false_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.summary is False
    assert cfg.components == ()


def test_reads_components_table(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "summary = true\n"
        "[[tool.semantic_release.bsr.components]]\n"
        'name = "api"\n'
        'paths = ["apps/api"]\n'
        "[[tool.semantic_release.bsr.components]]\n"
        'name = "web"\n'
        'paths = ["apps/web", "libs/shared"]\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.components == (
        BsrComponent(name="api", paths=("apps/api",)),
        BsrComponent(name="web", paths=("apps/web", "libs/shared")),
    )


def test_components_default_empty_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "summary = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.components == ()


def test_reads_stable_notes_flags(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "stable_notes_aggregate = true\n"
        'stable_notes_scope = "since_stable"\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.stable_notes_aggregate is True
    assert cfg.stable_notes_scope == "since_stable"


def test_invalid_stable_notes_scope_type_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        'stable_notes_scope = ["line"]\n',
    )

    with pytest.raises(InvalidConfiguration, match="stable_notes_scope"):
        load_bsr_config(cfg_file)


def test_stable_notes_defaults_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.stable_notes_aggregate is False
    assert cfg.stable_notes_scope == "line"


def test_reads_versioned_component_path_map(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[tool.semantic_release.bsr.component_path_map]\n"
        "schema_version = 1\n"
        'shared_policy = "none"\n'
        'root_policy = "none"\n'
        "[[tool.semantic_release.bsr.component_path_map.components]]\n"
        'id = "api"\n'
        'roots = ["apps/api"]\n'
        'release_paths = ["apps/api/pyproject.toml"]\n'
        'config_path = "apps/api/pyproject.toml"\n'
        "[[tool.semantic_release.bsr.component_path_map.rules]]\n"
        'kind = "component"\n'
        'path = "apps/api"\n'
        'components = ["api"]\n',
    )

    cfg = load_bsr_config(cfg_file)
    assert isinstance(cfg.component_path_map, ComponentPathMap)
    assert cfg.component_path_map.component_ids == ("api",)


def test_explicit_bsr_table_requires_schema_version(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "path_filter = true\n",
    )

    with pytest.raises(InvalidConfiguration, match="schema_version"):
        load_bsr_config(cfg_file)


def test_unknown_bsr_field_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "future_field = true\n",
    )

    with pytest.raises(InvalidConfiguration, match="future_field"):
        load_bsr_config(cfg_file)


def test_invalid_bsr_boolean_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        'path_filter = "true"\n',
    )

    with pytest.raises(InvalidConfiguration, match="path_filter"):
        load_bsr_config(cfg_file)


def test_invalid_bsr_paths_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        'paths = ["apps/api", 7]\n',
    )

    with pytest.raises(InvalidConfiguration, match="paths"):
        load_bsr_config(cfg_file)


def test_invalid_component_path_map_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr.component_path_map]\n"
        "schema_version = 2\n",
    )

    with pytest.raises(InvalidConfiguration, match="schema_version"):
        load_bsr_config(cfg_file)
