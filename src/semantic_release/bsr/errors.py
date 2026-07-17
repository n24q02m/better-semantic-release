from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semantic_release.version.version import Version


class BsrGuardError(Exception):
    """Raised when a bsr safety guard trips. Carries a user-facing message."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def bsr_silent_freeze_message(new_version: Version) -> str:
    """
    Build the message for the escalated silent-freeze failure.

    PSR was about to SILENTLY skip the release (exit 0, no error) because
    `new_version` is already released. This is the orphan-tag / rewritten-history
    silent-freeze: a rebase or force-push likely dropped `chore(release)` commits,
    orphaning the highest tag so PSR recomputes an already-consumed version.
    Instead of skipping green, the release is FAILED LOUD.
    """
    return (
        "better-semantic-release guard: SILENT RELEASE FREEZE PREVENTED.\n"
        f"  PSR was about to silently skip the release because {new_version!s} has "
        "already been released (would have exited 0, no error).\n"
        "  This is the orphan-tag / rewritten-history silent-freeze: a rebase or "
        "force-push likely dropped chore(release) commits, orphaning the highest "
        "tag so PSR recomputes an already-consumed version.\n"
        "  Failing loud instead of skipping green.\n"
        "  Fix: check for orphaned/rewritten release tags; re-tag the highest "
        "consumed tag onto a current ancestor, or seed a fresh reachable tag. To "
        "opt out, set [tool.semantic_release.bsr] guard_orphan_tag = false."
    )
