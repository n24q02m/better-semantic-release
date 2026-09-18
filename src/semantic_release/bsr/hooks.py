"""
Finite hook points (Task 8).

Five, and only five, hook points exist. Hooks are shell/argv steps the
repository configures; they never alter the plan decision -- a non-zero hook
exit blocks the run (fail closed). No other BSR code path is hookable on
purpose: a wide hook surface is un-auditable.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    from pathlib import Path

HOOK_POINTS = (
    "pre_plan",
    "post_plan",
    "pre_verify",
    "pre_publish",
    "post_publish",
)


@dataclass(frozen=True)
class HookSpec:
    """One configured hook: which point it fires at and what runs."""

    point: str
    command: tuple[str, ...]

    @property
    def display_command(self) -> str:
        return " ".join(self.command)


def parse_hooks(raw: object) -> tuple[HookSpec, ...]:
    """
    Parse ``[tool.semantic_release.bsr.hooks]`` config into ordered specs.

    Accepted shape::

        [[tool.semantic_release.bsr.hooks]]
        point = "pre_publish"
        command = ["./scripts/check-env.sh"]

    Unknown points and empty commands fail closed. Order of declaration is
    preserved (hooks at the same point run in config order).
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise InvalidConfiguration("bsr.hooks must be an array of tables")
    specs: list[HookSpec] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise InvalidConfiguration(
                f"bsr.hooks entry {index} must be a table"
            )
        point = str(entry.get("point", ""))
        if point not in HOOK_POINTS:
            raise InvalidConfiguration(
                f"bsr.hooks entry {index}: unknown point {point!r}; "
                f"must be one of: {', '.join(HOOK_POINTS)}"
            )
        command_raw = entry.get("command", ())
        if isinstance(command_raw, str):
            command: tuple[str, ...] = (command_raw,)
        elif isinstance(command_raw, (list, tuple)) and command_raw:
            command = tuple(str(item) for item in command_raw)
        else:
            raise InvalidConfiguration(
                f"bsr.hooks entry {index}: command must be a non-empty list"
            )
        seen[point] = seen.get(point, 0) + 1
        specs.append(HookSpec(point=point, command=command))
    return tuple(specs)


def run_hooks(
    hooks: tuple[HookSpec, ...],
    point: str,
    *,
    repo_dir: str | Path,
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """
    Run every hook declared for ``point`` in order.

    Returns ``(ok, detail)``. ``ok`` is True when all hooks exited 0 (or there
    were none); on the first non-zero exit the remaining hooks are skipped and
    ``detail`` names the hook and its exit code. Hook stdout/stderr are left to
    the process streams -- BSR does not swallow their output.
    """
    for hook in hooks:
        if hook.point != point:
            continue
        completed = subprocess.run(  # noqa: S603 - operator-committed config
            hook.command,
            cwd=str(repo_dir),
            check=False,
            env=dict(env) if env is not None else None,
        )
        if completed.returncode != 0:
            return (
                False,
                f"hook {point}:{hook.display_command} exited "
                f"{completed.returncode}",
            )
    return True, ""


__all__ = ["HOOK_POINTS", "HookSpec", "parse_hooks", "run_hooks"]
