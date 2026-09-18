"""Unit tests for publisher adapters and the typed outcome contract (Task 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from semantic_release.bsr.config import BsrPublishCommandConfig
from semantic_release.bsr.publishers import (
    OUTCOME_ALREADY_EXISTS,
    OUTCOME_ATTEMPTED,
    OUTCOME_FAILED,
    OUTCOME_RETRYABLE,
    OUTCOME_SKIPPED,
    PUBLISHER_KINDS,
    NoopPublisher,
    PublishOutcome,
    ShellPublisher,
    build_publishers,
    run_publishers,
)
from semantic_release.bsr.registry import ProbeResult
from semantic_release.errors import InvalidConfiguration


def test_outcome_states_are_typed() -> None:
    from semantic_release.bsr.publishers import OUTCOME_STATES

    expected_states = {
        OUTCOME_ATTEMPTED,
        OUTCOME_SKIPPED,
        OUTCOME_ALREADY_EXISTS,
        OUTCOME_FAILED,
        OUTCOME_RETRYABLE,
    }
    assert expected_states == OUTCOME_STATES


def test_publisher_kinds_covered() -> None:
    expected_kinds = {
        "none",
        "github-release",
        "pypi",
        "npm",
        "crates",
        "oci",
        "shell",
    }
    assert expected_kinds == PUBLISHER_KINDS


def test_noop_publisher_always_skips() -> None:
    outcome = NoopPublisher().publish("1.0.0", probe=None, noop=False)
    assert outcome.state == OUTCOME_SKIPPED


def test_shell_publisher_runs_and_reports_attempted(tmp_path) -> None:
    ran: list[list[str]] = []

    def runner(argv):
        ran.append(list(argv))
        return 0

    publisher = ShellPublisher(
        "shell", tmp_path, command=["./publish.sh"], runner=runner
    )
    outcome = publisher.publish("1.2.3", probe=None, noop=False)
    assert outcome.state == OUTCOME_ATTEMPTED
    assert ran == [["./publish.sh"]]


def test_shell_publisher_failed_and_retryable(tmp_path) -> None:
    ok = ShellPublisher("shell", tmp_path, command=["x"], runner=lambda _argv: 3)
    assert ok.publish("1.0.0", probe=None, noop=False).state == OUTCOME_FAILED

    retry = ShellPublisher("shell", tmp_path, command=["x"], runner=lambda _argv: 75)
    outcome = retry.publish("1.0.0", probe=None, noop=False)
    assert outcome.state == OUTCOME_RETRYABLE


def test_probe_gate_blocks_when_exists_or_unknown(tmp_path) -> None:
    ran: list[int] = []

    def runner(_argv):
        ran.append(1)
        return 0

    publisher = ShellPublisher("pypi", tmp_path, runner=runner)
    exists = publisher.publish("1.0.0", probe=ProbeResult.EXISTS, noop=False)
    assert exists.state == OUTCOME_ALREADY_EXISTS

    unknown = publisher.publish("1.0.0", probe=ProbeResult.UNKNOWN, noop=False)
    assert unknown.state == OUTCOME_SKIPPED
    assert "unknown" in unknown.detail

    assert ran == []


def test_noop_mode_never_runs_command(tmp_path) -> None:
    def runner(_argv):  # pragma: no cover - must not run
        raise AssertionError("runner must not be called under noop")

    publisher = ShellPublisher("shell", tmp_path, command=["x"], runner=runner)
    outcome = publisher.publish("1.0.0", probe=None, noop=True)
    assert outcome.state == OUTCOME_SKIPPED


def test_default_presets_for_registry_kinds(tmp_path) -> None:
    for kind, expected_head in [
        ("pypi", "python"),
        ("npm", "npm"),
        ("crates", "cargo"),
    ]:
        publisher = ShellPublisher(kind, tmp_path)
        assert publisher._command is not None
        assert publisher._command[0] == expected_head


def test_shell_and_oci_require_explicit_command(tmp_path) -> None:
    for kind in ("shell", "oci"):
        with pytest.raises(InvalidConfiguration, match="explicit command"):
            ShellPublisher(kind, tmp_path)


def test_unknown_publisher_kind_fails_closed(tmp_path) -> None:
    with pytest.raises(InvalidConfiguration, match="unknown publisher kind"):
        ShellPublisher("gemfury", tmp_path, command=["x"])


def test_github_release_publisher_requires_manifest_inputs(tmp_path) -> None:
    with pytest.raises(InvalidConfiguration, match="manifest_path"):
        build_publishers(
            [BsrPublishCommandConfig(kind="github-release")],
            repo_dir=tmp_path,
        )


def test_github_release_publisher_bridges_manifest(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".bsr-workspace").mkdir()
    calls: list[object] = []

    def fake_publish_manifest(manifest_path, *, workspace, **kwargs):
        calls.append((Path(manifest_path), Path(workspace)))
        return {"reconciled": True}

    monkeypatch.setattr(
        "semantic_release.bsr.release_publisher.publish_manifest",
        fake_publish_manifest,
    )
    publisher = build_publishers(
        [
            BsrPublishCommandConfig(
                kind="github-release",
                manifest_path="dist/manifest.json",
                workspace=".bsr-workspace",
            )
        ],
        repo_dir=tmp_path,
    )[0]
    outcome = publisher.publish("1.0.0", probe=None, noop=False)
    assert outcome.state == OUTCOME_ATTEMPTED, outcome
    manifest, workspace = calls[0]
    assert manifest == tmp_path / "dist" / "manifest.json"
    assert workspace == tmp_path / ".bsr-workspace"


def test_run_publishers_collects_outcomes(tmp_path) -> None:
    publishers = [
        NoopPublisher(),
        ShellPublisher("shell", tmp_path, command=["x"], runner=lambda _argv: 0),
    ]
    outcomes = run_publishers(publishers, "1.0.0", probe=None, noop=False)
    assert [outcome.state for outcome in outcomes] == [
        OUTCOME_SKIPPED,
        OUTCOME_ATTEMPTED,
    ]


def test_build_publishers_dispatch(tmp_path) -> None:
    publishers = build_publishers(
        [
            BsrPublishCommandConfig(kind="none"),
            BsrPublishCommandConfig(kind="shell", command=["x"]),
            BsrPublishCommandConfig(kind="npm"),
        ],
        repo_dir=tmp_path,
    )
    assert len(publishers) == 3
    assert isinstance(publishers[0], NoopPublisher)
    assert isinstance(publishers[1], ShellPublisher)
    assert isinstance(publishers[2], ShellPublisher)


def test_run_bsr_publishers_helper_wiring(tmp_path, monkeypatch) -> None:
    """The publish-command helper parses the tag and threads runtime ctx fields."""
    from types import SimpleNamespace

    from semantic_release.cli.commands.publish import _run_bsr_publishers
    from semantic_release.cli.config import RuntimeContext  # noqa: F401  (import guard)
    from semantic_release.version.translator import VersionTranslator

    seen: dict[str, object] = {}

    def fake_probe(
        kind, name, version, *, tag="", repo="", url_template="", registry_url=""
    ):
        seen.update(kind=kind, name=name, version=version, tag=tag, repo=repo)
        return ProbeResult.FREE

    monkeypatch.setattr("semantic_release.bsr.registry.probe_registry", fake_probe)

    def fake_run(publishers, version, *, probe, noop):
        seen["run_version"] = version
        seen["probe"] = probe
        return [PublishOutcome(state=OUTCOME_SKIPPED, detail="noop")]

    monkeypatch.setattr("semantic_release.bsr.publishers.run_publishers", fake_run)

    runtime = SimpleNamespace(
        version_translator=VersionTranslator(),
        project_metadata={"name": "demo"},
        repo_dir=tmp_path,
    )
    cli_ctx = SimpleNamespace(runtime_ctx=runtime)
    cfg = SimpleNamespace(
        probe=SimpleNamespace(
            kind="pypi", repo="", url_template="", registry_url=""
        ),
        publishers=(BsrPublishCommandConfig(kind="none"),),
    )
    failed = _run_bsr_publishers(cli_ctx, cfg, tag="v1.2.3", noop=False)
    assert failed is False
    assert seen["kind"] == "pypi"
    assert seen["name"] == "demo"
    assert seen["version"] == "1.2.3"
    assert seen["tag"] == "v1.2.3"
    assert seen["run_version"] == "1.2.3"


def test_run_bsr_publishers_helper_bad_tag_fails_closed(tmp_path) -> None:
    from types import SimpleNamespace

    from semantic_release.cli.commands.publish import _run_bsr_publishers
    from semantic_release.version.translator import VersionTranslator

    runtime = SimpleNamespace(
        version_translator=VersionTranslator(),
        project_metadata={"name": "demo"},
        repo_dir=tmp_path,
    )
    cfg = SimpleNamespace(
        probe=None,
        publishers=(BsrPublishCommandConfig(kind="none"),),
    )
    with pytest.raises(InvalidConfiguration, match="does not match tag format"):
        _run_bsr_publishers(SimpleNamespace(runtime_ctx=runtime), cfg, tag="1.2.3", noop=False)
