"""Docker action adapter for the standalone publisher runtime."""

from __future__ import annotations

import os

for _ambient_name in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "OPENSSL_ENGINES",
    "SSLKEYLOGFILE",
):
    os.environ.pop(_ambient_name, None)

# The image copies the runtime beside this adapter. During repository-local
# smoke tests the source tree is the fallback; both paths import only stdlib.
import sys  # noqa: E402 - ambient proxy/TLS sanitization must run first
from pathlib import Path  # noqa: E402 - ambient proxy/TLS sanitization must run first

_adapter_dir = Path(__file__).resolve().parent
_runtime_dir = (
    _adapter_dir
    if (_adapter_dir / "release_publisher.py").is_file()
    else _adapter_dir.parent / "src" / "semantic_release" / "bsr"
)
sys.path.insert(0, str(_runtime_dir))

from release_publisher import main  # noqa: E402 - import after sanitization

if __name__ == "__main__":
    raise SystemExit(main())
