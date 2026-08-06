from __future__ import annotations

import enum
import urllib.error
import urllib.parse
import urllib.request

_HTTP_OK = 200
_HTTP_NOT_FOUND = 404


class ProbeResult(enum.Enum):
    EXISTS = "exists"
    FREE = "free"
    UNKNOWN = "unknown"


def _http_status(url: str, timeout: float) -> int | None:
    """
    HTTP GET a URL and return its status code.

    Returns None on any network-level failure.
    """
    # Enforce runtime URL scheme validation to prevent SSRF or local file inclusion
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("http", "https"):
        return None

    req = urllib.request.Request(  # noqa: S310
        url, method="GET", headers={"User-Agent": "better-semantic-release-guard"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status: int | None = getattr(resp, "status", None)
            return status if status is not None else resp.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def probe_registry(
    registry: str, name: str, version: str, *, timeout: float = 10.0
) -> ProbeResult:
    """
    Probe a package registry for an existing (name, version) release.

    Fails closed to UNKNOWN on any ambiguous or unreachable state.
    """
    if registry == "pypi":
        url = (
            f"https://pypi.org/pypi/{urllib.parse.quote(name)}"
            f"/{urllib.parse.quote(version)}/json"
        )
    elif registry == "npm":
        url = (
            "https://registry.npmjs.org/"
            f"{urllib.parse.quote(name, safe='')}/{urllib.parse.quote(version)}"
        )
    else:
        return ProbeResult.FREE  # 'none' is short-circuited by the caller; defensive

    status = _http_status(url, timeout)
    if status == _HTTP_OK:
        return ProbeResult.EXISTS
    if status == _HTTP_NOT_FOUND:
        return ProbeResult.FREE
    return ProbeResult.UNKNOWN
