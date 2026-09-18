"""
Publish-readiness / provenance checks (Task 8).

Not an attestation factory. The value shipped here is a readiness probe: a
repository about to publish can ask, ahead of time, whether its environment
is configured the way the target requires -- the trusted-publishing posture
(OIDC token, no long-lived password) and whether the target supports
provenance at all. Anything unknown fails closed to ``ready=False`` with a
remediation detail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

PUBLISH_KINDS = ("none", "pypi", "npm", "crates", "oci", "shell", "github-release")

_OIDC_ENV_VARS = (
    "ACTIONS_ID_TOKEN_REQUEST_URL",  # GitHub Actions OIDC
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
)

# registries that accept PEP 740 / npm provenance attestations
_PROVENANCE_CAPABLE = frozenset({"pypi", "npm"})


@dataclass(frozen=True)
class ReadinessResult:
    """Outcome of one publish-readiness assessment."""

    ready: bool
    supports_provenance: bool | None = None
    detail: str = ""


def supports_provenance(publish_kind: str) -> bool | None:
    """
    Whether ``publish_kind`` supports provenance attestations.

    ``None`` = unknown/irrelevant (e.g. ``none`` or a custom ``shell`` step).
    """
    if publish_kind in _PROVENANCE_CAPABLE:
        return True
    if publish_kind in ("none", "shell"):
        return None
    return False


def assess_publish_readiness(
    publish_kind: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> ReadinessResult:
    """
    Check that the current environment matches ``publish_kind``'s posture.

    Rules (fail closed):

    - ``pypi``/``npm``: trusted publishing expects an OIDC token request
      environment. A ``*_PASSWORD``/``_TOKEN`` env fallback is reported as
      not-ready detail (long-lived secrets are the posture BSR discourages).
    - ``crates``/``oci``/``shell``: require their credential variable; presence
      means ready. Provenance support is False/None accordingly.
    - ``none``/``github-release``: trivially ready.
    """
    if publish_kind not in PUBLISH_KINDS:
        raise ValueError(
            f"unknown publish kind {publish_kind!r}; must be one of: "
            + ", ".join(PUBLISH_KINDS)
        )
    env = os.environ if environment is None else environment

    if publish_kind in ("none", "github-release"):
        return ReadinessResult(ready=True)

    provenance = supports_provenance(publish_kind)

    if publish_kind in ("pypi", "npm"):
        has_oidc = all(env.get(var) for var in _OIDC_ENV_VARS)
        if has_oidc:
            return ReadinessResult(
                ready=True,
                supports_provenance=provenance,
                detail="trusted publishing (OIDC) environment detected",
            )
        legacy = env.get("PYPI_PASSWORD") or env.get("NPM_TOKEN")
        if legacy:
            return ReadinessResult(
                ready=False,
                supports_provenance=provenance,
                detail=(
                    "legacy password/token detected; migrate to trusted "
                    "publishing (OIDC) for this target"
                ),
            )
        return ReadinessResult(
            ready=False,
            supports_provenance=provenance,
            detail="no OIDC environment and no legacy credential configured",
        )

    credential_var = {
        "crates": "CARGO_REGISTRY_TOKEN",
        "oci": "OCI_REGISTRY_PASSWORD",
        "shell": None,
    }.get(publish_kind)

    if credential_var is None:
        return ReadinessResult(ready=True, supports_provenance=provenance)
    if env.get(credential_var):
        return ReadinessResult(ready=True, supports_provenance=provenance)
    return ReadinessResult(
        ready=False,
        supports_provenance=provenance,
        detail=f"missing {credential_var}",
    )


__all__ = [
    "PUBLISH_KINDS",
    "ReadinessResult",
    "assess_publish_readiness",
    "supports_provenance",
]
