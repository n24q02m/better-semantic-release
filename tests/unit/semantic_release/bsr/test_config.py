from __future__ import annotations

from typing import TYPE_CHECKING

from semantic_release.bsr.config import BsrComponent, BsrConfig, load_bsr_config

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
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.explain is False
    assert cfg.actionable_errors is False


def test_missing_file_returns_defaults(tmp_path: Path):
    assert load_bsr_config(tmp_path / "nope.toml") == BsrConfig()


def test_malformed_config_returns_defaults(tmp_path: Path):
    cfg_file = _write(tmp_path, "this is not valid toml :::\n")
    assert load_bsr_config(cfg_file) == BsrConfig()


def test_reads_summary_flag(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "summary = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.summary is True


def test_summary_defaults_false_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
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
        "summary = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.components == ()
