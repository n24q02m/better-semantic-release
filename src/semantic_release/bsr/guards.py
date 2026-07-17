from __future__ import annotations

from typing import TYPE_CHECKING

from git import Repo

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.bsr.orphan_tag import check_orphan_tag
from semantic_release.bsr.registry import ProbeResult, probe_registry

if TYPE_CHECKING:
    from semantic_release.bsr.config import BsrConfig
    from semantic_release.cli.config import RuntimeContext
    from semantic_release.version.version import Version

_VALID_REGISTRIES = {"pypi", "npm", "none"}


def resolve_registry(bsr_config: BsrConfig, project_name: str) -> str:
    """Resolve the effective registry name from config + project name."""
    registry = (bsr_config.registry or "").strip().lower()
    if registry in _VALID_REGISTRIES:
        return registry
    if registry == "":
        return "pypi" if project_name else "none"
    raise BsrGuardError(
        f"better-semantic-release guard: invalid [tool.semantic_release.bsr] registry "
        f"'{bsr_config.registry}'. Use one of: pypi, npm, none."
    )


def run_guards(
    *, runtime: RuntimeContext, new_version: Version, bsr_config: BsrConfig
) -> None:
    """
    Run enabled safety guards. Raise BsrGuardError on any trip.

    Called from the version command AFTER the next version is computed but
    BEFORE any file write / commit / tag / push.
    """
    if bsr_config.guard_orphan_tag:
        with Repo(str(runtime.repo_dir)) as repo:
            check_orphan_tag(repo, runtime.version_translator)

    if bsr_config.guard_registry_collision:
        project_name = runtime.project_metadata.get("name", "") or ""
        registry = resolve_registry(bsr_config, project_name)
        if registry != "none":
            version_str = str(new_version)
            result = probe_registry(registry, project_name, version_str)
            if result is ProbeResult.EXISTS:
                raise BsrGuardError(
                    "better-semantic-release guard: VERSION ALREADY PUBLISHED.\n"
                    f"  {project_name}@{version_str} already exists on {registry}.\n"
                    "  PSR would try to re-publish an existing version (history rewrite / "
                    "collision). Aborting before commit/tag/push.\n"
                    "  Fix: verify tag history reachability and registry state; do not "
                    "re-cut a consumed version."
                )
            if result is ProbeResult.UNKNOWN:
                raise BsrGuardError(
                    "better-semantic-release guard: REGISTRY PROBE UNKNOWN (fail-closed).\n"
                    f"  Could not confirm whether {project_name}@{version_str} is free on "
                    f"{registry} (network/rate-limit/5xx).\n"
                    "  Treating UNKNOWN as free risks a double publish, so this aborts. "
                    "Retry, or set [tool.semantic_release.bsr] registry='none' if intentional."
                )
