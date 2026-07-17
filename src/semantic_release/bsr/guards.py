from __future__ import annotations

from typing import TYPE_CHECKING

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.bsr.registry import ProbeResult, probe_registry

if TYPE_CHECKING:
    from pathlib import Path

    from semantic_release.bsr.config import BsrConfig
    from semantic_release.cli.config import RuntimeContext
    from semantic_release.version.translator import VersionTranslator
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


def is_orphaned_recompute(
    repo_dir: Path, translator: VersionTranslator, new_version: Version
) -> bool:
    """
    Detect a genuine orphan/rewritten-tag silent-freeze (vs. a benign no-op).

    `new_version` is already known to be in `previously_released_versions` (an
    already-released version) by the time this is called. That alone does not
    mean anything is wrong: a benign no-op re-dispatch (no new releasable
    commits) recomputes `new_version` == the highest version reachable from
    HEAD, and PSR's silent skip there is correct.

    The dangerous case is when a rebase/force-push dropped the `chore(release)`
    commits, orphaning the highest tag: PSR then bumps up from whatever base
    IS still reachable and lands on a version that already exists as an
    unreachable tag. That is only possible when `new_version` is HIGHER than
    everything reachable from HEAD (or nothing is reachable at all).

    Returns True (orphaned -- fire the guard) when `new_version` is higher than
    the highest version reachable from HEAD, or when no tag is reachable at
    all. Returns False (benign no-op -- stay silent) when `new_version` equals
    the reachable tip.
    """
    from git import Repo  # noqa: PLC0415

    from semantic_release.version.algorithm import tags_and_versions  # noqa: PLC0415

    with Repo(str(repo_dir)) as repo:
        head = repo.head.commit
        reachable = [
            v
            for tag, v in tags_and_versions(repo.tags, translator)
            if repo.is_ancestor(tag.commit, head)
        ]

    if not reachable:
        return True  # tags exist (already-released) but none reachable -> orphaned

    return new_version > max(reachable)


def run_guards(
    *, runtime: RuntimeContext, new_version: Version, bsr_config: BsrConfig
) -> None:
    """
    Run enabled safety guards. Raise BsrGuardError on any trip.

    Called from the version command AFTER the next version is computed but
    BEFORE any file write / commit / tag / push.

    Note: the orphan/rewritten-history silent-freeze case is handled earlier, in
    `cli.commands.version`, by escalating PSR's own "already released" skip to a
    loud failure (gated on `bsr_config.guard_orphan_tag`) -- by the time this
    function runs, PSR has already returned for that case, so no reachability
    check belongs here.
    """
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
