"""
Registry probe adapters (Task 5).

One probe adapter per ecosystem, resolved through :data:`PROBE_ADAPTERS`.
Every probe answers the same three-value question -- does ``(name, version)``
already exist, is it free, or is the state ambiguous (fail closed to
:attr:`ProbeResult.UNKNOWN`) -- regardless of backend.
"""

from __future__ import annotations

import enum
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

_HTTP_OK = 200
_HTTP_NOT_FOUND = 404


class ProbeResult(enum.Enum):
    EXISTS = "exists"
    FREE = "free"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeTarget:
    """Everything a probe adapter may need to answer for one (name, version)."""

    kind: str
    name: str
    version: str
    # Full release tag as published (e.g. "v1.2.3"); required for the
    # github-release probe, optional elsewhere.
    tag: str = ""
    # github-release: "owner/repo" slug
    repo: str = ""
    # http: URL template with {name} and {version} placeholders
    url_template: str = ""
    # oci: registry host, e.g. "ghcr.io" or "registry-1.docker.io"
    registry_url: str = ""


def _http_status(
    url: str, timeout: float, extra_headers: dict[str, str] | None = None
) -> int | None:
    """
    HTTP GET a URL and return its status code.

    Returns None on any network-level failure.
    """
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme not in ("http", "https"):
        return None

    headers = {"User-Agent": "better-semantic-release-guard"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(  # noqa: S310
        url, method="GET", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status: int | None = getattr(resp, "status", None)
            return status if status is not None else resp.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _classify_status(status: int | None) -> ProbeResult:
    if status == _HTTP_OK:
        return ProbeResult.EXISTS
    if status == _HTTP_NOT_FOUND:
        return ProbeResult.FREE
    return ProbeResult.UNKNOWN


def _require(target: ProbeTarget, field: str) -> str:
    value = getattr(target, field)
    if not value:
        raise ValueError(
            f"probe kind {target.kind!r} requires {field!r} to be configured"
        )
    return value


def probe_pypi(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    url = (
        f"https://pypi.org/pypi/{urllib.parse.quote(target.name)}"
        f"/{urllib.parse.quote(target.version)}/json"
    )
    return _classify_status(_http_status(url, timeout))


def probe_npm(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    url = (
        "https://registry.npmjs.org/"
        f"{urllib.parse.quote(target.name, safe='')}/{urllib.parse.quote(target.version)}"
    )
    return _classify_status(_http_status(url, timeout))


def probe_crates(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    url = (
        f"https://crates.io/api/v1/crates/{urllib.parse.quote(target.name, safe='')}"
        f"/{urllib.parse.quote(target.version, safe='')}"
    )
    headers = {"Accept": "application/json"}
    return _classify_status(_http_status(url, timeout, headers))


def probe_oci(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    registry = _require(target, "registry_url")
    # OCI repository names contain literal slashes; escape only each path segment.
    name = "/".join(
        urllib.parse.quote(part, safe="") for part in target.name.split("/")
    )
    url = (
        f"https://{registry}/v2/{name}"
        f"/manifests/{urllib.parse.quote(target.version, safe='')}"
    )
    headers = {"Accept": "application/vnd.oci.image.index.v1+json"}
    return _classify_status(_http_status(url, timeout, headers))


def probe_github_release(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    repo = _require(target, "repo")
    tag = target.tag or _require(target, "tag")
    url = (
        f"https://api.github.com/repos/{repo}"
        f"/releases/tags/{urllib.parse.quote(tag, safe='')}"
    )
    return _classify_status(_http_status(url, timeout))


def probe_http(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    template = _require(target, "url_template")
    url = template.format(
        name=urllib.parse.quote(target.name, safe=""),
        version=urllib.parse.quote(target.version, safe=""),
    )
    return _classify_status(_http_status(url, timeout))


def probe_none(target: ProbeTarget, timeout: float = 10.0) -> ProbeResult:
    del target, timeout
    return ProbeResult.FREE


ProbeFn = Callable[[ProbeTarget, float], ProbeResult]

PROBE_KINDS = frozenset(
    {"none", "pypi", "npm", "crates", "oci", "github-release", "http"}
)

PROBE_ADAPTERS: dict[str, ProbeFn] = {
    "none": probe_none,
    "pypi": probe_pypi,
    "npm": probe_npm,
    "crates": probe_crates,
    "oci": probe_oci,
    "github-release": probe_github_release,
    "http": probe_http,
}


def probe_registry(
    registry: str,
    name: str,
    version: str,
    *,
    timeout: float = 10.0,
    tag: str = "",
    repo: str = "",
    url_template: str = "",
    registry_url: str = "",
) -> ProbeResult:
    """
    Probe a package registry for an existing (name, version) release.

    Dispatches to the adapter for ``registry``; unknown kinds raise instead of
    silently reporting FREE. Fails closed to UNKNOWN on any ambiguous or
    unreachable state.
    """
    if registry not in PROBE_ADAPTERS:
        raise ValueError(
            f"unknown registry probe kind {registry!r}; must be one of: "
            + ", ".join(sorted(PROBE_KINDS))
        )
    target = ProbeTarget(
        kind=registry,
        name=name,
        version=version,
        tag=tag,
        repo=repo,
        url_template=url_template,
        registry_url=registry_url,
    )
    return PROBE_ADAPTERS[registry](target, timeout)


__all__ = [
    "PROBE_ADAPTERS",
    "PROBE_KINDS",
    "ProbeFn",
    "ProbeResult",
    "ProbeTarget",
    "probe_registry",
]
