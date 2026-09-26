"""
Tag-race / concurrent-runner guard (`bsr.tag_race`, W1.6, spec §4.6).

Before `version` tags or pushes anything, the remote state must be re-read
and matched against the state this run planned from:

- the planned tag already exists on the remote → ``TAG_RACE`` (another
  runner already cut this release);
- the remote branch head moved since this run started →
  ``CONCURRENT_RUNNER`` (another runner pushed commits while we prepared);
- the remote state cannot be confirmed (fetch/transport failure, unknown
  remote) → ``TAG_RACE_UNCONFIRMED`` — fail closed (spec P3).

The check is one read-only ``git ls-remote`` against the configured remote:
a single atomic snapshot of the remote's heads and tags, no local mutation.
Tests run it against local-path remotes (no real network).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from git import Repo
from git.exc import GitCommandError

if TYPE_CHECKING:
    from pathlib import Path


class TagRaceGuardError(Exception):
    """The remote state drifted from the planned state (or was unconfirmable)."""

    def __init__(self, code: str, message: str, remediation: str) -> None:
        self.code = code
        self.message = message
        self.remediation = remediation
        super().__init__(f"[{code}] {message}")


def _parse_ls_remote(output: object) -> dict[str, str]:
    """Parse `git ls-remote` output into {refname: sha} (peel lines dropped)."""
    if not isinstance(output, str):
        raise TypeError(f"unexpected ls_remote output type {type(output).__name__}")
    refs: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        sha, _, refname = line.partition("\t")
        if refname.endswith("^{}"):
            continue
        refs[refname.strip()] = sha.strip()
    return refs


def check_tag_race(
    repo_dir: Path | str,
    *,
    remote_name: str,
    branch: str,
    planned_tag: str,
    planned_head_sha: str,
    noop: bool = False,
) -> None:
    """
    Re-read the remote and fail closed on drift from the planned state.

    :param planned_head_sha: the local HEAD this run planned from (BEFORE the
        release commit, if any), i.e. what the remote branch is expected to
        still point at.
    :raises TagRaceGuardError: on any drift or on an unconfirmable remote.
    """
    if noop:
        return

    refs: dict[str, str]
    try:
        with Repo(str(repo_dir)) as repo:
            output = repo.git.ls_remote(remote_name)
            refs = _parse_ls_remote(output)
    except (GitCommandError, ValueError, TypeError) as exc:
        raise TagRaceGuardError(
            code="TAG_RACE_UNCONFIRMED",
            message=(f"could not confirm remote state of '{remote_name}': " f"{exc}"),
            remediation=(
                "verify remote connectivity/credentials and re-run; do not "
                "publish without confirmation"
            ),
        ) from exc

    tag_ref = f"refs/tags/{planned_tag}"
    if tag_ref in refs:
        raise TagRaceGuardError(
            code="TAG_RACE",
            message=(
                f"tag {planned_tag!r} already exists on remote '{remote_name}' "
                f"({refs[tag_ref]}); another runner likely already cut this release"
            ),
            remediation=(
                "re-run `plan` to recompute the release; never re-push an "
                "existing release tag"
            ),
        )

    branch_ref = f"refs/heads/{branch}"
    remote_head = refs.get(branch_ref)
    if remote_head is not None and remote_head != planned_head_sha:
        raise TagRaceGuardError(
            code="CONCURRENT_RUNNER",
            message=(
                f"remote branch '{remote_name}/{branch}' moved since this run "
                f"planned ({remote_head} != {planned_head_sha})"
            ),
            remediation="rebase on the remote state, re-plan, and re-run",
        )
