"""
Integration wiring tests (wave-2): config fields -> dispatch arms.

These cover the parent integration commit's surface that the individual
W2.x PRs deliberately left out: real ``BsrPublishCommandConfig`` objects
(no duck-typed stand-ins) flowing through ``build_publishers``, and the
``mode``/``gated`` config surface parsing end to end via
``load_bsr_config``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from semantic_release.bsr.config import (
    BsrGatedConfig,
    BsrPublishCommandConfig,
    load_bsr_config,
)
from semantic_release.bsr.gating import DEFAULT_GATE_ENVIRONMENT
from semantic_release.bsr.oci_publisher import OciPublisher
from semantic_release.bsr.publishers import ShellPublisher, build_publishers
from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    from pathlib import Path


class TestOciDispatch:
    def test_real_config_with_image_builds_oci_publisher(self, tmp_path: Path) -> None:
        config = BsrPublishCommandConfig(kind="oci", image="ghcr.io/acme/widgets")
        publishers = build_publishers([config], repo_dir=tmp_path)
        assert len(publishers) == 1
        assert isinstance(publishers[0], OciPublisher)
        assert publishers[0].name == "oci"

    def test_oci_without_image_keeps_legacy_shell_flow(self, tmp_path: Path) -> None:
        config = BsrPublishCommandConfig(kind="oci", command=("echo", "hi"))
        publishers = build_publishers([config], repo_dir=tmp_path)
        assert isinstance(publishers[0], ShellPublisher)

    def test_oci_config_fields_survive_registration(self, tmp_path: Path) -> None:
        config = BsrPublishCommandConfig(
            kind="oci",
            image="ghcr.io/acme/widgets",
            tags=(" {version} ",),
            push=True,
        )
        publisher = build_publishers([config], repo_dir=tmp_path)[0]
        assert isinstance(publisher, OciPublisher)
        assert publisher._config.push is True


class TestGatedModeConfig:
    def _write(self, tmp_path: Path, body: str) -> Path:
        config = tmp_path / "pyproject.toml"
        config.write_text(body, encoding="utf-8")
        return config

    def test_defaults_are_release_mode(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path, '[tool.semantic_release]\nversion_variable = "v.py:__version__"\n'
        )
        bsr_cfg = load_bsr_config(config)
        assert bsr_cfg.mode == "release"
        assert bsr_cfg.gated is None

    def test_gated_mode_parses(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            '[tool.semantic_release.bsr]\nschema_version = 1\nmode = "gated"\n',
        )
        bsr_cfg = load_bsr_config(config)
        assert bsr_cfg.mode == "gated"
        assert bsr_cfg.gated is None

    def test_gated_table_parses_environment(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            '[tool.semantic_release.bsr]\nschema_version = 1\nmode = "gated"\n'
            "[tool.semantic_release.bsr.gated]\n"
            'environment = "prod-gate"\n',
        )
        bsr_cfg = load_bsr_config(config)
        assert bsr_cfg.gated == BsrGatedConfig(environment="prod-gate")

    def test_gated_table_defaults_to_gate_environment(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            '[tool.semantic_release.bsr]\nschema_version = 1\nmode = "gated"\n'
            "[tool.semantic_release.bsr.gated]\n",
        )
        bsr_cfg = load_bsr_config(config)
        assert bsr_cfg.gated == BsrGatedConfig(environment=DEFAULT_GATE_ENVIRONMENT)

    def test_unknown_mode_fails_closed(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            '[tool.semantic_release.bsr]\nschema_version = 1\nmode = "yolo"\n',
        )
        with pytest.raises(InvalidConfiguration, match=r"\.mode must be one of"):
            load_bsr_config(config)

    def test_unknown_gated_field_fails_closed(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            '[tool.semantic_release.bsr]\nschema_version = 1\nmode = "gated"\n'
            "[tool.semantic_release.bsr.gated]\n"
            'timeout = "5m"\n',
        )
        with pytest.raises(InvalidConfiguration, match="unknown"):
            load_bsr_config(config)


class TestOciConfigParsing:
    def _write(self, tmp_path: Path, body: str) -> Path:
        config = tmp_path / "pyproject.toml"
        config.write_text(body, encoding="utf-8")
        return config

    def test_oci_publisher_table_parses_all_fields(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            "[tool.semantic_release.bsr]\nschema_version = 1\n"
            "[tool.semantic_release.bsr.publish]\n"
            "[[tool.semantic_release.bsr.publish.publishers]]\n"
            'kind = "oci"\n'
            'image = "ghcr.io/acme/widgets"\n'
            'tags = ["{version}", "latest"]\n'
            'dockerfile = "deploy/Dockerfile"\n'
            'context = "src"\n'
            "push = true\n"
            'output = "out/img.oci.tar"\n',
        )
        bsr_cfg = load_bsr_config(config)
        publisher = bsr_cfg.publish.publishers[0]
        assert publisher.image == "ghcr.io/acme/widgets"
        assert publisher.tags == ("{version}", "latest")
        assert publisher.dockerfile == "deploy/Dockerfile"
        assert publisher.context == "src"
        assert publisher.push is True
        assert publisher.output == "out/img.oci.tar"
        registered = build_publishers([publisher], repo_dir=tmp_path)
        assert isinstance(registered[0], OciPublisher)

    def test_oci_field_type_mismatch_fails_closed(self, tmp_path: Path) -> None:
        config = self._write(
            tmp_path,
            "[tool.semantic_release.bsr]\nschema_version = 1\n"
            "[tool.semantic_release.bsr.publish]\n"
            "[[tool.semantic_release.bsr.publish.publishers]]\n"
            'kind = "oci"\n'
            'image = "ghcr.io/acme/widgets"\n'
            'push = "yes"\n',
        )
        with pytest.raises(InvalidConfiguration, match="push must be a boolean"):
            load_bsr_config(config)
