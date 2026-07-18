from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from semantic_release.cli.util import load_raw_config_file

if TYPE_CHECKING:
    import os


@dataclass(frozen=True)
class BsrConfig:
    guard_orphan_tag: bool = True
    guard_registry_collision: bool = True
    registry: str = ""  # "", "pypi", "npm", or "none"
    path_filter: bool = False
    paths: tuple[str, ...] = ()
    explain: bool = False
    actionable_errors: bool = False


def load_bsr_config(config_file: str | os.PathLike[str]) -> BsrConfig:
    """
    Read [tool.semantic_release.bsr] out of the config file.

    PSR's RawConfig uses pydantic extra='ignore' and DROPS this table, so we
    re-parse independently. Any failure -> all-default BsrConfig (guards on).
    """
    try:
        # Reuse PSR's parser: returns the [tool.semantic_release] dict.
        sr = load_raw_config_file(Path(config_file))
    except Exception:  # noqa: BLE001
        return BsrConfig()

    bsr = sr.get("bsr", {}) if isinstance(sr, dict) else {}
    if not isinstance(bsr, dict):
        return BsrConfig()

    return BsrConfig(
        guard_orphan_tag=bool(bsr.get("guard_orphan_tag", True)),
        guard_registry_collision=bool(bsr.get("guard_registry_collision", True)),
        registry=str(bsr.get("registry", "")),
        path_filter=bool(bsr.get("path_filter", False)),
        paths=tuple(bsr.get("paths", [])),
        explain=bool(bsr.get("explain", False)),
        actionable_errors=bool(bsr.get("actionable_errors", False)),
    )
