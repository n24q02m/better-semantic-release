"""Docker action adapter for the standalone publisher runtime."""

from __future__ import annotations

import sys
from pathlib import Path

# The image copies the runtime beside this adapter. During repository-local
# smoke tests the source tree is the fallback; both paths import only stdlib.
_adapter_dir = Path(__file__).resolve().parent
_runtime_dir = (
    _adapter_dir
    if (_adapter_dir / "release_publisher.py").is_file()
    else _adapter_dir.parent / "src" / "semantic_release" / "bsr"
)
sys.path.insert(0, str(_runtime_dir))

from release_publisher import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
