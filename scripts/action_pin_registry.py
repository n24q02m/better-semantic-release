"""Command-line entry point for the BSR action-pin registry tools."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "semantic_release"
    / "bsr"
    / "action_pin_registry.py"
)
_SPEC = importlib.util.spec_from_file_location("_bsr_action_pin_registry", _MODULE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("registry module loader unavailable")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
main = _MODULE.main

if __name__ == "__main__":
    raise SystemExit(main())
