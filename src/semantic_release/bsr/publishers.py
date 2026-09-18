"""
Publisher adapters (Task 5): typed, idempotent publish steps.

The publisher layer is separate from the registry-probe layer on purpose: a
repo may probe PyPI but publish a GitHub release plus a shell command, or have
no registry at all and only publish tags/assets.

Every publisher returns a :class:`PublishOutcome` whose ``state`` is one of:

- ``attempted``      -- the publish command ran and exited zero;
- ``skipped``        -- policy said not to run (no-op mode, fail-closed
  unknown registry state, explicit no-op publisher);
- ``already-exists`` -- the probe found the release already present;
- ``failed``         -- the command ran and failed hard;
- ``retryable``      -- the command failed in a way that is safe to retry
  (transport timeout / EX_TEMPFAIL exit code 75).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence

from semantic_release.bsr.registry import ProbeResult
from semantic_release.errors import InvalidConfiguration

OUTCOME_ATTEMPTED = "attempted"
OUTCOME_SKIPPED = "skipped"
OUTCOME_ALREADY_EXISTS = "already-exists"
OUTCOME_FAILED = "failed"
OUTCOME_RETRYABLE = "retryable"

OUTCOME_STATES = frozenset(
    {
        OUTCOME_ATTEMPTED,
        OUTCOME_SKIPPED,
        OUTCOME_ALREADY_EXISTS,
        OUTCOME_FAILED,
        OUTCOME_RETRYABLE,
    }
)

PUBLISHER_KINDS = frozenset(
    {"none", "github-release", "pypi", "npm", "crates", "oci", "shell"}
)

# Exit code conventionally meaning temporary failure, safe to retry.
_EX_TEMPFAIL = 75

_DEFAULT_COMMANDS: dict[str, tuple[str, ...]] = {
    "pypi": ("python", "-m", "twine", "upload", "dist/*"),
    "npm": ("npm", "publish"),
    "crates": ("cargo", "publish"),
}

_REGISTRY_BACKED_KINDS = frozenset({"pypi", "npm", "crates", "oci", "shell"})


@dataclass(frozen=True)
class PublishOutcome:
    """Typed result of one publisher step; callers never parse prose."""

    state: str
    detail: str = ""


CommandRunner = Callable[[Sequence[str]], int]


class Publisher(Protocol):
    """Write-side publish contract: one labeled, idempotent publish step."""

    name: str
    kind: str

    def publish(
        self, version: str, *, probe: ProbeResult | None, noop: bool
    ) -> PublishOutcome: ...


def _default_runner(command: Sequence[str], cwd: Path) -> int:
    return subprocess.run(  # noqa: S603 - argv from explicit user config
        list(command), cwd=str(cwd), check=False
    ).returncode


class NoopPublisher:
    """Explicit ``none`` publisher: policy layer, never runs anything."""

    name = "none"
    kind = "none"

    def publish(
        self, version: str, *, probe: ProbeResult | None, noop: bool
    ) -> PublishOutcome:
        del version, probe, noop
        return PublishOutcome(OUTCOME_SKIPPED, "no-op publisher configured")


class ShellPublisher:
    """Runs an explicit argv (or a per-ecosystem preset) in the repo."""

    def __init__(
        self,
        kind: str,
        repo_dir: Path | str,
        *,
        command: Sequence[str] = (),
        name: str = "",
        runner: CommandRunner | None = None,
    ) -> None:
        if kind not in PUBLISHER_KINDS:
            raise InvalidConfiguration(
                f"unknown publisher kind {kind!r}; must be one of: "
                + ", ".join(sorted(PUBLISHER_KINDS))
            )
        self.kind = kind
        self.name = name or kind
        self._repo_dir = Path(repo_dir)
        self._runner = runner or (lambda argv: _default_runner(argv, self._repo_dir))
        if command:
            self._command: tuple[str, ...] | None = tuple(command)
        else:
            if kind in {"shell", "oci", "github-release"}:
                raise InvalidConfiguration(
                    f"publisher kind {kind!r} requires an explicit command; "
                    "no safe default exists"
                )
            self._command = _DEFAULT_COMMANDS[kind]

    def publish(
        self, version: str, *, probe: ProbeResult | None, noop: bool
    ) -> PublishOutcome:
        if noop:
            return PublishOutcome(OUTCOME_SKIPPED, "no-op mode; command not run")
        if probe is ProbeResult.EXISTS:
            return PublishOutcome(
                OUTCOME_ALREADY_EXISTS, f"{self.name}: registry already has {version}"
            )
        if probe is ProbeResult.UNKNOWN and self.kind in _REGISTRY_BACKED_KINDS:
            return PublishOutcome(
                OUTCOME_SKIPPED,
                f"{self.name}: registry state unknown; refusing to publish blind",
            )
        del version
        exit_code = self._runner(self._command or ())
        if exit_code == 0:
            return PublishOutcome(OUTCOME_ATTEMPTED, f"{self.name}: exited 0")
        if exit_code == _EX_TEMPFAIL:
            return PublishOutcome(
                OUTCOME_RETRYABLE, f"{self.name}: exited {_EX_TEMPFAIL} (tempfail)"
            )
        return PublishOutcome(
            OUTCOME_FAILED, f"{self.name}: exited {exit_code}"
        )


class GithubReleasePublisher:
    """Bridge to the strict-manifest GitHub release flow (release_publisher)."""

    kind = "github-release"

    def __init__(
        self,
        repo_dir: Path | str,
        *,
        manifest_path: Path | str,
        workspace: Path | str,
        name: str = "",
        publish_manifest: Callable[..., object] | None = None,
    ) -> None:
        self.name = name or "github-release"
        self._repo_dir = Path(repo_dir)
        manifest = Path(manifest_path)
        if not manifest.is_absolute():
            manifest = self._repo_dir / manifest
        workspace = Path(workspace)
        if not workspace.is_absolute():
            workspace = self._repo_dir / workspace
        self._manifest_path = manifest
        self._workspace = workspace
        self._publish_manifest = publish_manifest

    def publish(
        self, version: str, *, probe: ProbeResult | None, noop: bool
    ) -> PublishOutcome:
        del probe  # release reconciliation is idempotent by design
        if noop:
            return PublishOutcome(OUTCOME_SKIPPED, "no-op mode; release not published")
        if self._publish_manifest is None:
            from semantic_release.bsr.release_publisher import publish_manifest

            self._publish_manifest = publish_manifest
        try:
            self._publish_manifest(
                self._manifest_path,
                workspace=self._workspace,
            )
        except Exception as exc:  # noqa: BLE001 - provider errors are value-safe
            detail = str(getattr(exc, "code", "") or exc)
            return PublishOutcome(OUTCOME_FAILED, f"{self.name}: {detail}")
        return PublishOutcome(OUTCOME_ATTEMPTED, f"{self.name}: release {version} reconciled")


def build_publishers(
    publisher_configs: Sequence[object],
    *,
    repo_dir: Path | str,
    runner: CommandRunner | None = None,
) -> list[Publisher]:
    """Resolve ``[tool.bsr.publish]`` publisher configs into adapter instances."""
    from semantic_release.bsr.config import BsrPublishCommandConfig

    publishers: list[Publisher] = []
    for config in publisher_configs:
        if not isinstance(config, BsrPublishCommandConfig):  # pragma: no cover
            raise TypeError(f"unsupported publisher config: {type(config).__name__}")
        if config.kind == "none":
            publishers.append(NoopPublisher())
        elif config.kind == "github-release":
            if not config.manifest_path or not config.workspace:
                raise InvalidConfiguration(
                    "publisher github-release requires manifest_path and workspace"
                )
            publishers.append(
                GithubReleasePublisher(
                    repo_dir,
                    manifest_path=config.manifest_path,
                    workspace=config.workspace,
                    name=config.name,
                )
            )
        else:
            publishers.append(
                ShellPublisher(
                    config.kind,
                    repo_dir,
                    command=tuple(config.command),
                    name=config.name,
                    runner=runner,
                )
            )
    return publishers


def run_publishers(
    publishers: Sequence[Publisher],
    version: str,
    *,
    probe: ProbeResult | None,
    noop: bool,
) -> list[PublishOutcome]:
    """Run every publisher in order and collect typed outcomes."""
    return [
        publisher.publish(version, probe=probe, noop=noop) for publisher in publishers
    ]


__all__ = [
    "OUTCOME_STATES",
    "PUBLISHER_KINDS",
    "GithubReleasePublisher",
    "NoopPublisher",
    "PublishOutcome",
    "Publisher",
    "ShellPublisher",
    "build_publishers",
    "run_publishers",
]
