"""
Unit tests for the OCI publisher (wave 2, W2.2).

Covers config normalization/validation, ref templating, the publish
decision table (noop / probe states / builder detection / push guard) and
the deterministic provenance-tar fallback layout. All builder seams are
injected fakes: no test shells out to docker/podman or the network.
"""

from __future__ import annotations

import dataclasses
import json
import tarfile
from pathlib import Path

import pytest

from semantic_release.bsr.oci_publisher import (
    OciPublisher,
    OciPublisherConfig,
    register_publisher,
    write_provenance_oci_tar,
)
from semantic_release.bsr.publishers import (
    OUTCOME_ALREADY_EXISTS,
    OUTCOME_ATTEMPTED,
    OUTCOME_FAILED,
    OUTCOME_RETRYABLE,
    OUTCOME_SKIPPED,
)
from semantic_release.bsr.registry import ProbeResult
from semantic_release.errors import InvalidConfiguration

IMAGE = "ghcr.io/acme/widgets"


@dataclasses.dataclass(frozen=True)
class _Cfg:
    """
    Duck-typed stand-in for the pre-wiring config surface.

    config.py wiring (image/tags/... fields) is the parent integration
    commit's job per the packet contract.
    """

    kind: str = "oci"
    name: str = ""
    command: tuple[str, ...] = ()
    image: str = ""
    tags: list[str] | None = None
    dockerfile: str = ""
    context: str = ""
    push: bool = False
    output: str = ""


def _recording_runner(codes: dict[tuple[str, ...], int] | None = None):
    """Fake runner; codes match by argv PREFIX (real builder argv is long)."""
    calls: list[list[str]] = []

    def runner(argv) -> int:
        argv = list(argv)
        calls.append(argv)
        for prefix, code in (codes or {}).items():
            if tuple(argv[: len(prefix)]) == prefix:
                return code
        return 0

    return runner, calls


def _detector(available: tuple[str, ...] = ()):
    return lambda binary: f"/usr/bin/{binary}" if binary in available else None


def _publisher(tmp_path: Path, **kwargs) -> tuple[OciPublisher, list[list[str]]]:
    runner, calls = _recording_runner()
    publisher = register_publisher(
        _Cfg(**kwargs),
        repo_dir=tmp_path,
        runner=runner,
        detector=_detector(),
    )
    assert isinstance(publisher, OciPublisher)
    return publisher, calls


class TestRegistration:
    def test_image_required(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidConfiguration, match="requires an 'image'"):
            register_publisher(_Cfg(), repo_dir=tmp_path)

    @pytest.mark.parametrize("image", [f"{IMAGE}:v1", f"{IMAGE}@sha256:abc"])
    def test_image_rejects_tag_or_digest(self, tmp_path: Path, image: str) -> None:
        with pytest.raises(InvalidConfiguration, match="must not carry a tag"):
            register_publisher(_Cfg(image=image), repo_dir=tmp_path)

    def test_command_rejected_for_oci_kind(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidConfiguration, match="kind 'shell'"):
            register_publisher(
                _Cfg(image=IMAGE, command=("echo",)),
                repo_dir=tmp_path,
            )

    def test_tags_must_be_strings(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidConfiguration, match="array of strings"):
            register_publisher(
                _Cfg(image=IMAGE, tags=[1, 2]),  # type: ignore[list-item]
                repo_dir=tmp_path,
            )

    def test_paths_resolve_against_repo_dir(self, tmp_path: Path) -> None:
        pub, _ = _publisher(
            tmp_path, image=IMAGE, dockerfile="deploy/Dockerfile", context="src"
        )
        assert pub._config.dockerfile == tmp_path / "deploy" / "Dockerfile"
        assert pub._config.context == tmp_path / "src"

    def test_default_artifact_path_slugs_the_image(self, tmp_path: Path) -> None:
        pub, _ = _publisher(tmp_path, image="ghcr.io/acme/my widget")
        assert pub._artifact_path() == tmp_path / "dist" / "my-widget.oci.tar"

    def test_explicit_output_wins(self, tmp_path: Path) -> None:
        pub, _ = _publisher(tmp_path, image=IMAGE, output="out/img.oci.tar")
        assert pub._artifact_path() == tmp_path / "out" / "img.oci.tar"


class TestRefs:
    def test_version_template_substitution(self, tmp_path: Path) -> None:
        pub, _ = _publisher(tmp_path, image=IMAGE, tags=["{version}", "latest"])
        assert pub._refs("1.2.3") == [f"{IMAGE}:1.2.3", f"{IMAGE}:latest"]

    def test_default_tag_is_version(self, tmp_path: Path) -> None:
        pub, _ = _publisher(tmp_path, image=IMAGE)
        assert pub._refs("1.2.3") == [f"{IMAGE}:1.2.3"]

    def test_invalid_resolved_tag_fails_closed(self, tmp_path: Path) -> None:
        pub, _ = _publisher(tmp_path, image=IMAGE, tags=["{version}"])
        with pytest.raises(InvalidConfiguration, match="not a valid tag"):
            pub._refs("1.2.3 extra")


class TestPublishDecisions:
    def test_noop_skips_without_building(self, tmp_path: Path) -> None:
        pub, calls = _publisher(tmp_path, image=IMAGE)
        outcome = pub.publish("1.0.0", probe=None, noop=True)
        assert outcome.state == OUTCOME_SKIPPED
        assert calls == []

    def test_probe_exists_short_circuits(self, tmp_path: Path) -> None:
        pub, calls = _publisher(tmp_path, image=IMAGE)
        outcome = pub.publish("1.0.0", probe=ProbeResult.EXISTS, noop=False)
        assert outcome.state == OUTCOME_ALREADY_EXISTS
        assert calls == []

    def test_unknown_probe_refuses_blind_push(self, tmp_path: Path) -> None:
        pub, calls = _publisher(tmp_path, image=IMAGE, push=True)
        outcome = pub.publish("1.0.0", probe=ProbeResult.UNKNOWN, noop=False)
        assert outcome.state == OUTCOME_SKIPPED
        assert calls == []

    def test_docker_buildx_builds_and_pushes_requested_refs(
        self, tmp_path: Path
    ) -> None:
        runner, calls = _recording_runner()
        pub = register_publisher(
            _Cfg(image=IMAGE, tags=["{version}", "latest"], push=True),
            repo_dir=tmp_path,
            runner=runner,
            detector=_detector(("docker",)),
        )
        outcome = pub.publish("2.0.0", probe=ProbeResult.FREE, noop=False)
        assert outcome.state == OUTCOME_ATTEMPTED
        assert "pushed 2 ref(s)" in outcome.detail
        builds = [c for c in calls if c[:3] == ["docker", "buildx", "build"]]
        assert builds, f"buildx build must be invoked; got {calls}"
        assert any("--output" in arg for arg in builds[0])

    def test_buildx_tempfail_is_retryable(self, tmp_path: Path) -> None:
        runner, _ = _recording_runner(codes={("docker", "buildx", "build"): 75})
        pub = register_publisher(
            _Cfg(image=IMAGE),
            repo_dir=tmp_path,
            runner=runner,
            detector=_detector(("docker",)),
        )
        outcome = pub.publish("2.0.0", probe=ProbeResult.FREE, noop=False)
        assert outcome.state == OUTCOME_RETRYABLE

    def test_buildx_hard_fail_is_failed(self, tmp_path: Path) -> None:
        runner, _ = _recording_runner(codes={("docker", "buildx", "build"): 1})
        pub = register_publisher(
            _Cfg(image=IMAGE),
            repo_dir=tmp_path,
            runner=runner,
            detector=_detector(("docker",)),
        )
        outcome = pub.publish("2.0.0", probe=ProbeResult.FREE, noop=False)
        assert outcome.state == OUTCOME_FAILED

    def test_podman_fallback_when_no_buildx(self, tmp_path: Path) -> None:
        runner, calls = _recording_runner()
        pub = register_publisher(
            _Cfg(image=IMAGE),
            repo_dir=tmp_path,
            runner=runner,
            detector=_detector(("podman",)),
        )
        outcome = pub.publish("2.0.0", probe=ProbeResult.FREE, noop=False)
        assert outcome.state == OUTCOME_ATTEMPTED
        assert calls[0][0] == "podman"

    def test_no_builder_writes_provenance_tar(self, tmp_path: Path) -> None:
        pub, calls = _publisher(tmp_path, image=IMAGE)
        outcome = pub.publish("2.0.0", probe=ProbeResult.FREE, noop=False)
        assert outcome.state == OUTCOME_ATTEMPTED
        assert "provenance-only" in outcome.detail
        assert calls == []
        artifact = tmp_path / "dist" / "widgets.oci.tar"
        assert artifact.is_file()

    def test_push_without_builder_fails_hard(self, tmp_path: Path) -> None:
        pub, _ = _publisher(tmp_path, image=IMAGE, push=True)
        outcome = pub.publish("2.0.0", probe=ProbeResult.FREE, noop=False)
        assert outcome.state == OUTCOME_FAILED
        assert "no docker/podman builder" in outcome.detail


class TestProvenanceTar:
    def _write(self, destination: Path) -> None:
        write_provenance_oci_tar(
            destination,
            image=IMAGE,
            refs=[f"{IMAGE}:1.0.0"],
            dockerfile=Path("Dockerfile"),
            context=Path("."),
        )

    def test_layout_carries_oci_layout_index_and_blobs(self, tmp_path: Path) -> None:
        artifact = tmp_path / "x.oci.tar"
        self._write(artifact)
        with tarfile.open(artifact) as tar:
            names = set(tar.getnames())
            assert "oci-layout" in names
            assert "index.json" in names
            assert any(name.startswith("blobs/sha256/") for name in names)
            layout = json.loads(tar.extractfile("oci-layout").read().decode())  # type: ignore[union-attr]
            # mediaType is OPTIONAL in the oci-layout spec; buildx omits it too
            assert layout.get("imageLayoutVersion") == "1.0.0"
            index = json.loads(tar.extractfile("index.json").read().decode())  # type: ignore[union-attr]
            assert index["mediaType"] == "application/vnd.oci.image.index.v1+json"
            assert index["manifests"]

    def test_layout_is_deterministic(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a.oci.tar", tmp_path / "b.oci.tar"
        self._write(first)
        self._write(second)
        assert first.read_bytes() == second.read_bytes()


class TestConfigDataclass:
    def test_defaults(self) -> None:
        config = OciPublisherConfig(image=IMAGE)
        assert config.tags == ("{version}",)
        assert config.push is False
        assert config.dockerfile == Path("Dockerfile")
