"""
OCI publisher (wave-2 W2.2): container-image publish step for ``kind = "oci"``.

Design notes:

- This module lives next to :mod:`semantic_release.bsr.publishers` (flat layout)
  instead of inside a ``publishers/`` package on purpose: a package of the same
  name would shadow the existing flat module and break the public API.
- Registration is one dispatch arm in ``build_publishers`` (see module
  ``publishers.py``); the parent integration commit adds::

      elif config.kind == "oci" and config.image:
          publishers.append(register_publisher(config, repo_dir=repo_dir))

  ``kind = "oci"`` configs that still only carry a legacy ``command`` keep
  flowing to ``ShellPublisher`` unchanged, so nothing breaks before that line
  lands.

Config surface (``[[tool.bsr.publish.publishers]]`` with ``kind = "oci"``)::

    image = "ghcr.io/owner/repo"  # required; no tag or @digest suffix
    tags = ["{version}", "latest"]  # optional; default ["{version}"]
    dockerfile = "Dockerfile"  # optional; repo-relative
    context = "."  # optional; repo-relative build context
    push = true  # optional; default false
    output = "dist/x.oci.tar"  # optional; default dist/<slug>.oci.tar

The publisher always produces a *tar artifact* in OCI image layout
(``oci-layout``, ``index.json``, ``blobs/sha256/*`` -- the ``skret`` release
pipeline's ``*.oci.tar`` pattern). The builder is duck-typed:

- ``docker`` + ``buildx`` -> ``docker buildx build --output type=oci[,type=registry]``
- ``podman`` -> ``podman build`` + ``podman save --format oci-archive``
  (+ ``podman push`` when ``push = true``)
- neither -> a deterministic pure-Python OCI-layout tar carrying a provenance
  layer; ``push = true`` without a builder fails hard instead of pretending.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from semantic_release.bsr.registry import ProbeResult
from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    from semantic_release.bsr.publishers import (
        Publisher,
        PublishOutcome,
    )

_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_EX_TEMPFAIL = 75

# Duck-typed seams: both are injectable for tests and never required.
BinaryDetector = Callable[[str], str | None]
CommandRunner = Callable[[Sequence[str]], int]

_DOCKER = "docker"
_PODMAN = "podman"


@dataclass(frozen=True)
class OciPublisherConfig:
    """Normalized ``kind = "oci"`` publisher config (paths already resolved)."""

    image: str
    name: str = "oci"
    tags: tuple[str, ...] = ("{version}",)
    dockerfile: Path = Path("Dockerfile")
    context: Path = Path(".")
    push: bool = False
    output: Path | None = None


def _as_bool(value: object, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise InvalidConfiguration(f"bsr.publish.publishers: oci.{field} must be a boolean")


def _resolve_path(repo_dir: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise InvalidConfiguration(
            f"bsr.publish.publishers: oci.{field} must be a non-empty string"
        )
    path = Path(raw)
    return path if path.is_absolute() else repo_dir / path


def register_publisher(
    config: object,
    *,
    repo_dir: Path | str,
    runner: CommandRunner | None = None,
    detector: BinaryDetector | None = None,
) -> Publisher:
    """
    Build an :class:`OciPublisher` from a ``BsrPublishCommandConfig``.

    Duck-typed on purpose: accepts any object exposing ``image``, ``tags``,
    ``dockerfile``, ``context``, ``push`` and ``output`` attributes, so this
    module stays usable even before ``config.py`` grows the fields.
    """
    repo = Path(repo_dir)
    image = str(getattr(config, "image", "") or "")
    if not image:
        raise InvalidConfiguration(
            "bsr.publish.publishers: kind 'oci' requires an 'image' reference "
            "(e.g. ghcr.io/owner/repo)"
        )
    if ":" in image.rsplit("/", 1)[-1] or "@" in image:
        raise InvalidConfiguration(
            f"bsr.publish.publishers: oci.image {image!r} must not carry a tag or "
            "digest; tags go in oci.tags"
        )
    if tuple(getattr(config, "command", ()) or ()):
        raise InvalidConfiguration(
            "bsr.publish.publishers: kind 'oci' takes image/tags/dockerfile/"
            "context; use kind 'shell' for a raw command"
        )
    raw_tags = getattr(config, "tags", None)
    if raw_tags is None:
        tags: tuple[str, ...] = ()
    elif isinstance(raw_tags, (list, tuple)) and all(
        isinstance(tag, str) for tag in raw_tags
    ):
        tags = tuple(raw_tags)
    else:
        raise InvalidConfiguration(
            "bsr.publish.publishers: oci.tags must be an array of strings"
        )
    dockerfile = _resolve_path(
        repo, getattr(config, "dockerfile", "") or "Dockerfile", "dockerfile"
    )
    context = _resolve_path(repo, getattr(config, "context", "") or ".", "context")
    push = _as_bool(getattr(config, "push", False), "push")
    raw_output = getattr(config, "output", "") or ""
    output = _resolve_path(repo, raw_output, "output") if raw_output else None
    name = str(getattr(config, "name", "") or "oci")
    return OciPublisher(
        OciPublisherConfig(
            image=image,
            name=name,
            tags=tags,
            dockerfile=dockerfile,
            context=context,
            push=push,
            output=output,
        ),
        repo,
        runner=runner,
        detector=detector,
    )


def _default_detector(binary: str) -> str | None:
    return shutil.which(binary)


def _default_runner(argv: Sequence[str], cwd: Path) -> int:
    return subprocess.run(  # noqa: S603 - argv built from validated config
        list(argv), cwd=str(cwd), check=False
    ).returncode


def _exit_outcome(exit_code: int, name: str, detail: str) -> PublishOutcome:
    from semantic_release.bsr.publishers import (
        OUTCOME_ATTEMPTED,
        OUTCOME_FAILED,
        OUTCOME_RETRYABLE,
        PublishOutcome,
    )

    if exit_code == 0:
        return PublishOutcome(OUTCOME_ATTEMPTED, detail)
    if exit_code == _EX_TEMPFAIL:
        return PublishOutcome(
            OUTCOME_RETRYABLE, f"{name}: exited {_EX_TEMPFAIL} (tempfail)"
        )
    return PublishOutcome(OUTCOME_FAILED, f"{name}: exited {exit_code}")


def _slug(image: str) -> str:
    tail = image.rsplit("/", 1)[-1] or "image"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-.")
    return slug or "image"


def _blob(payload: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(payload).hexdigest()
    return f"blobs/sha256/{digest}", f"sha256:{digest}"


def _tar_add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = 0
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(payload))


def _tar_add_dir(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mtime = 0
    info.mode = 0o755
    archive.addfile(info)


def write_provenance_oci_tar(
    destination: Path,
    *,
    image: str,
    refs: Sequence[str],
    dockerfile: Path,
    context: Path,
) -> None:
    """
    Write a minimal, deterministic OCI-layout tar without a container builder.

    The image is a scratch image whose single layer is a tar containing
    ``oci-provenance.json``. ``oci-layout``, ``index.json`` and the blob set are
    produced exactly like a ``buildx --output type=oci`` export so downstream
    tooling (skopeo, syft, ``oci-archive:`` sources) treats both artifacts alike.
    """
    provenance = json.dumps(
        {
            "publisher": "bsr.oci_publisher",
            "image": image,
            "refs": list(refs),
            "dockerfile": str(dockerfile),
            "context": str(context),
            "note": "no docker/podman builder on PATH; provenance-only image",
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    layer_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=layer_buffer, mode="w", format=tarfile.USTAR_FORMAT
    ) as layer_tar:
        _tar_add_bytes(layer_tar, "oci-provenance.json", provenance)
    layer_bytes = layer_buffer.getvalue()
    layer_name, layer_digest = _blob(layer_bytes)

    config_bytes = json.dumps(
        {
            "architecture": "unknown",
            "os": "unknown",
            "config": {},
            "rootfs": {
                "type": "layers",
                "diff_ids": [layer_digest],
            },
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    config_name, config_digest = _blob(config_bytes)

    manifest_bytes = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config_bytes),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": layer_digest,
                    "size": len(layer_bytes),
                }
            ],
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    manifest_name, manifest_digest = _blob(manifest_bytes)

    index_bytes = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": manifest_digest,
                    "size": len(manifest_bytes),
                    "annotations": {
                        "org.opencontainers.image.ref.name": ref,
                    },
                }
                for ref in refs
            ],
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w", format=tarfile.USTAR_FORMAT) as archive:
        _tar_add_bytes(archive, "oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
        _tar_add_bytes(archive, "index.json", index_bytes)
        _tar_add_dir(archive, "blobs")
        _tar_add_dir(archive, "blobs/sha256")
        _tar_add_bytes(archive, config_name, config_bytes)
        _tar_add_bytes(archive, manifest_name, manifest_bytes)
        _tar_add_bytes(archive, layer_name, layer_bytes)


class OciPublisher:
    """``kind = "oci"`` publisher: build an OCI-layout tar, optionally push."""

    kind = "oci"

    def __init__(
        self,
        config: OciPublisherConfig,
        repo_dir: Path | str,
        *,
        runner: CommandRunner | None = None,
        detector: BinaryDetector | None = None,
    ) -> None:
        self.name = config.name
        self._config = config
        self._repo_dir = Path(repo_dir)
        self._runner = runner or (lambda argv: _default_runner(argv, self._repo_dir))
        self._detector = detector or _default_detector

    def _refs(self, version: str) -> list[str]:
        tags = self._config.tags or ("{version}",)
        refs: list[str] = []
        for template in tags:
            tag = template.replace("{version}", version)
            if not _TAG_RE.match(tag):
                raise InvalidConfiguration(
                    f"{self.name}: resolved OCI tag {tag!r} is not a valid tag "
                    f"(template {template!r}, version {version!r})"
                )
            refs.append(f"{self._config.image}:{tag}")
        return refs

    def _artifact_path(self) -> Path:
        if self._config.output is not None:
            return self._config.output
        return self._repo_dir / "dist" / f"{_slug(self._config.image)}.oci.tar"

    def _detect_builder(self) -> str:
        """Return 'docker', 'podman' or '' after duck-typing the environment."""
        if (
            self._detector(_DOCKER) is not None
            and self._runner((_DOCKER, "buildx", "version")) == 0
        ):
            return _DOCKER
        if self._detector(_PODMAN) is not None:
            return _PODMAN
        return ""

    def _precondition_outcome(
        self, version: str, *, probe: ProbeResult | None, noop: bool
    ) -> PublishOutcome | None:
        """Short-circuit outcomes decided before any builder runs."""
        from semantic_release.bsr.publishers import (  # noqa: TCH001
            OUTCOME_ALREADY_EXISTS,
            OUTCOME_SKIPPED,
            PublishOutcome,
        )

        if noop:
            return PublishOutcome(OUTCOME_SKIPPED, "no-op mode; image not built")
        if probe is ProbeResult.EXISTS:
            return PublishOutcome(
                OUTCOME_ALREADY_EXISTS,
                f"{self.name}: registry already has {version}",
            )
        if probe is ProbeResult.UNKNOWN and self._config.push:
            return PublishOutcome(
                OUTCOME_SKIPPED,
                f"{self.name}: registry state unknown; refusing to publish blind",
            )
        return None

    def publish(
        self, version: str, *, probe: ProbeResult | None, noop: bool
    ) -> PublishOutcome:
        from semantic_release.bsr.publishers import (  # noqa: TCH001
            OUTCOME_FAILED,
            PublishOutcome,
        )

        precondition = self._precondition_outcome(version, probe=probe, noop=noop)
        if precondition is not None:
            return precondition

        refs = self._refs(version)
        artifact = self._artifact_path()
        builder = self._detect_builder()

        if builder == _DOCKER:
            code = self._build_with_buildx(refs, artifact)
            detail = f"{self.name}: wrote {artifact.name}"
            if self._config.push:
                detail += f"; pushed {len(refs)} ref(s)"
            return _exit_outcome(code, self.name, detail)
        if builder == _PODMAN:
            code = self._build_with_podman(refs[0], artifact)
            if code != 0:
                return _exit_outcome(code, self.name, "")
            if self._config.push:
                push_outcome = self._push_with_podman(refs)
                if push_outcome.state != "attempted":
                    return push_outcome
                detail = push_outcome.detail
            else:
                detail = ""
            return _exit_outcome(
                0,
                self.name,
                detail or f"{self.name}: wrote {artifact.name}",
            )

        write_provenance_oci_tar(
            artifact,
            image=self._config.image,
            refs=refs,
            dockerfile=self._config.dockerfile,
            context=self._config.context,
        )
        if self._config.push:
            return PublishOutcome(
                OUTCOME_FAILED,
                f"{self.name}: push requested but no docker/podman builder on PATH",
            )
        return PublishOutcome(
            "attempted",
            f"{self.name}: wrote {artifact.name} (no builder; provenance-only)",
        )

    def _build_with_buildx(self, refs: Sequence[str], artifact: Path) -> int:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            _DOCKER,
            "buildx",
            "build",
            "-f",
            str(self._config.dockerfile),
            "--output",
            f"type=oci,dest={artifact}",
        ]
        for ref in refs:
            argv.extend(["-t", ref])
        if self._config.push:
            argv.extend(["--output", "type=registry"])
        argv.append(str(self._config.context))
        return self._runner(argv)

    def _build_with_podman(self, ref: str, artifact: Path) -> int:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        code = self._runner(
            [
                _PODMAN,
                "build",
                "-f",
                str(self._config.dockerfile),
                "-t",
                ref,
                str(self._config.context),
            ]
        )
        if code != 0:
            return code
        return self._runner(
            [
                _PODMAN,
                "save",
                "--format",
                "oci-archive",
                "--output",
                str(artifact),
                ref,
            ]
        )

    def _push_with_podman(self, refs: Sequence[str]) -> PublishOutcome:
        from semantic_release.bsr.publishers import PublishOutcome

        for ref in refs:
            code = self._runner([_PODMAN, "push", ref])
            if code != 0:
                return _exit_outcome(code, self.name, "")
        return PublishOutcome("attempted", f"{self.name}: pushed {len(refs)} ref(s)")


__all__ = [
    "OciPublisher",
    "OciPublisherConfig",
    "register_publisher",
    "write_provenance_oci_tar",
]
