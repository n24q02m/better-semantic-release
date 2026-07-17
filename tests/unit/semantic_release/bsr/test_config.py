from __future__ import annotations

from pathlib import Path

from semantic_release.bsr.config import BsrConfig, load_bsr_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_defaults_when_no_bsr_table(tmp_path: Path):
    cfg_file = _write(tmp_path, '[tool.semantic_release]\ntag_format = "v{version}"\n')
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(guard_orphan_tag=True, guard_registry_collision=True, registry="")


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
    assert cfg == BsrConfig(guard_orphan_tag=False, guard_registry_collision=True, registry="npm")


def test_missing_file_returns_defaults(tmp_path: Path):
    assert load_bsr_config(tmp_path / "nope.toml") == BsrConfig()


def test_malformed_config_returns_defaults(tmp_path: Path):
    cfg_file = _write(tmp_path, "this is not valid toml :::\n")
    assert load_bsr_config(cfg_file) == BsrConfig()
