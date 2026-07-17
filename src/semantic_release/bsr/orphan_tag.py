from __future__ import annotations

from typing import TYPE_CHECKING

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.version.algorithm import tags_and_versions

if TYPE_CHECKING:
    from git import Repo

    from semantic_release.version.translator import VersionTranslator


def check_orphan_tag(repo: Repo, translator: VersionTranslator) -> None:
    """
    Fail if the highest existing tag is unreachable from HEAD (Case 1).

    PSR computes the next version off the last tag REACHABLE from HEAD, not the
    highest global tag. A rebase/force-push that drops chore(release) commits
    orphans the highest tag -> PSR recomputes an already-used version -> silent
    release freeze. This guard turns that silent freeze into a loud exit 1.
    Runs regardless of PSR's `released` flag.
    """
    tv = tags_and_versions(repo.tags, translator)  # semver-sorted descending
    if not tv:
        return  # no matching tags yet -> nothing can be orphaned

    highest_tag, highest_version = tv[0]
    head = repo.head.commit
    if not repo.is_ancestor(highest_tag.commit, head):
        raise BsrGuardError(
            "better-semantic-release guard: ORPHANED RELEASE TAG.\n"
            f"  Highest tag '{highest_tag.name}' (v{highest_version}) is NOT reachable "
            f"from HEAD ({head.hexsha[:8]}).\n"
            "  PSR would recompute an already-published version and silently skip the "
            "release. History was likely rebased/force-pushed, dropping chore(release) "
            "commits.\n"
            "  Fix: re-tag the highest consumed tag onto a current main ancestor, or seed "
            "a fresh minor tag reachable from HEAD. Then re-run. Prefer merge over rebase "
            "on release branches."
        )
