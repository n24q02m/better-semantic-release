from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cd.yml"
DOCKERFILE = ROOT / "publish-action" / "Dockerfile"
PUBLISH_ACTION = ROOT / "publish-action" / "action.yml"
REGISTRY_ACTION = ROOT / "registry-action" / "action.yml"
REGISTRY_SCRIPT = ROOT / "scripts" / "action_pin_registry.py"
PUBLISHER_DIGEST = (
    "sha256:93f7c8780a8b6e6a6c21c7eac38cbde94bbe90c1123211273cdcfaa505aa5427"
)


def assert_pinned_action(uses: str, action: str) -> None:
    """
    Require ``action`` at a full commit SHA, whichever release the SHA is.

    Renovate bumps the SHA through ``pinDigests``; the contract is that the
    reference stays an immutable commit of the expected action, not one
    particular release.
    """
    assert re.fullmatch(rf"{re.escape(action)}@[0-9a-f]{{40}}", uses), uses


def _load_workflow() -> dict[str, object]:
    # BaseLoader intentionally preserves string-only workflow semantics.
    return yaml.load(
        WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,  # noqa: S506
    )


def test_manual_cd_operations_are_mutually_exclusive_and_gated() -> None:
    document = _load_workflow()

    dispatch = document["on"]["workflow_dispatch"]
    operation = dispatch["inputs"]["operation"]
    assert operation["default"] == "release"
    assert operation["options"] == [
        "release",
        "publisher-image",
        "registry-g1",
        "upstream-sync",
    ]
    for input_name in ("registry_tag", "candidate_run_id", "candidate_run_attempt"):
        assert dispatch["inputs"][input_name]["required"] == "false"

    assert (
        document["jobs"]["release"]["if"]
        == "inputs.operation == 'release' && (inputs.parent_run_id != '' || needs.inspect.outputs.refresh != 'true')"
    )
    image = document["jobs"]["publisher-image"]
    assert image["if"] == (
        "inputs.operation == 'publisher-image' && github.ref == 'refs/heads/main'"
    )
    assert image["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert image["environment"]["name"] == "beta-publish"
    buildx = next(
        step
        for step in image["steps"]
        if step.get("name") == "Set up isolated BuildKit builder"
    )
    assert_pinned_action(buildx["uses"], "docker/setup-buildx-action")

    registry = document["jobs"]["registry-g1"]
    assert registry["if"] == (
        "inputs.operation == 'registry-g1' && github.ref == 'refs/heads/main'"
    )
    assert registry["environment"]["name"] == "beta-publish"
    assert registry["permissions"] == {
        "actions": "read",
        "attestations": "read",
        "contents": "write",
        "id-token": "write",
        "packages": "read",
    }


def test_release_route_has_no_image_permissions_or_image_steps() -> None:
    document = _load_workflow()
    release = document["jobs"]["release"]
    assert (
        release["if"]
        == "inputs.operation == 'release' && (inputs.parent_run_id != '' || needs.inspect.outputs.refresh != 'true')"
    )
    assert release["permissions"] == {"contents": "write", "id-token": "write"}
    assert "packages" not in release.get("permissions", {})
    assert all(
        "publish-action" not in str(step.get("uses", ""))
        or "python-semantic-release/publish-action" in str(step.get("uses", ""))
        for step in release["steps"]
    )
    release_text = "\n".join(
        f"{step.get('name', '')} {step.get('run', '')} {step.get('uses', '')}"
        for step in release["steps"]
    ).lower()
    assert "gh release" not in release_text


def test_final_publisher_action_pins_verified_candidate_digest() -> None:
    action = yaml.safe_load(PUBLISH_ACTION.read_text(encoding="utf-8"))
    assert action["inputs"]["manifest"]["required"] is True
    assert action["inputs"]["token"]["required"] is True
    assert action["runs"]["using"] == "docker"
    assert action["runs"]["image"] == (
        f"docker://ghcr.io/n24q02m/better-semantic-release-publisher@{PUBLISHER_DIGEST}"
    )
    assert action["runs"]["args"] == ["--manifest", "${{ inputs.manifest }}"]
    assert action["runs"]["env"] == {"INPUT_TOKEN": "${{ inputs.token }}"}


def test_registry_action_is_a_self_contained_remote_loader() -> None:
    action = yaml.safe_load(REGISTRY_ACTION.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    assert (
        action["outputs"]["registry"]["value"] == "${{ steps.load.outputs.registry }}"
    )
    installer, loader = action["runs"]["steps"]
    assert_pinned_action(installer["uses"], "sigstore/cosign-installer")
    assert loader["id"] == "load"
    command = loader["run"]
    assert "scripts/action_pin_registry.py" in command
    assert 'python3 -I -S "$GITHUB_ACTION_PATH' in command
    assert 'action_pin_registry.py" load' in command
    assert "--expected" in command
    assert "--repository" in command


def test_registry_cli_boots_without_project_dependencies() -> None:
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-S", str(REGISTRY_SCRIPT), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "build a canonical registry record" in completed.stdout


def test_g1_job_signs_publishes_and_fresh_loads_create_once_pair() -> None:
    document = _load_workflow()
    registry = document["jobs"]["registry-g1"]
    text = "\n".join(
        f"{step.get('name', '')} {step.get('run', '')} {step.get('uses', '')}"
        for step in registry["steps"]
    ).lower()
    assert "gh run download" in text
    assert "scripts/action_pin_registry.py generate" in text
    assert "cosign sign-blob" in text
    assert "cosign verify-blob" in text
    assert "tampered" in text
    assert "./publish-action" in text
    assert "./registry-action" in text
    assert "g1-release-manifest.json" in text
    assert "g1-loader-result.json" in text
    assert "action_pin_registry.py head" in text
    assert "previous-registry.json" in text
    assert "--previous previous-registry.json" in text
    assert '--exclude-tag "$registry_tag"' in text
    assert ".[0].generation == .[1].generation" in text
    assert ".generation == 1" not in text
    upload = next(
        step
        for step in registry["steps"]
        if step.get("name") == "Upload signed G1 evidence"
    )
    assert_pinned_action(upload["uses"], "actions/upload-artifact")
    assert upload["with"]["if-no-files-found"] == "error"


def test_g1_release_manifest_preserves_publisher_schema_key_order() -> None:
    document = _load_workflow()
    registry = document["jobs"]["registry-g1"]
    manifest_step = next(
        step
        for step in registry["steps"]
        if step.get("name") == "Build exact create-once release manifest"
    )
    command = manifest_step["run"]
    assert "sort_keys=False" in command
    assert "sort_keys=True" not in command


def test_g1_release_manifest_uses_publisher_asset_digest_format() -> None:
    document = _load_workflow()
    registry = document["jobs"]["registry-g1"]
    manifest_step = next(
        step
        for step in registry["steps"]
        if step.get("name") == "Build exact create-once release manifest"
    )
    command = manifest_step["run"]
    assert '"sha256": "sha256:" + hashlib.sha256(content).hexdigest()' in command


def test_candidate_job_contains_only_image_bootstrap_mutations() -> None:
    document = _load_workflow()
    image = document["jobs"]["publisher-image"]
    raw_step_text = "\n".join(
        f"{step.get('name', '')} {step.get('run', '')} {step.get('uses', '')}"
        for step in image["steps"]
    )
    step_text = raw_step_text.lower()
    assert "pypi" not in step_text
    assert "gh release" not in step_text
    assert "semantic-release publish" not in step_text
    assert "id -u" in step_text
    assert "id -g" in step_text
    assert '--build-arg "publisher_uid=' in step_text
    assert '--build-arg "publisher_gid=' in step_text
    assert '--build-arg "SOURCE_DATE_EPOCH=0"' in raw_step_text
    assert "docker buildx build" in step_text
    assert "--no-cache" in step_text
    assert "rewrite-timestamp=true" in step_text
    assert "repro-first" in step_text
    assert "{{.id}}" in step_text
    assert "{{json .rootfs.layers}}" in step_text
    assert 'test "$first_id" = "$second_id"' in step_text
    assert 'test "$first_layers" = "$second_layers"' in step_text
    assert "github-output" in step_text
    assert "preflight" in step_text
    assert '[[ ! "$subject_digest" =~ ^sha256:[0-9a-f]{64}$ ]]' in step_text
    assert 'remote_digest="$(docker buildx imagetools inspect "$image"' in step_text
    assert '[[ ! "$remote_digest" =~ ^sha256:[0-9a-f]{64}$ ]]' in step_text
    assert '"$remote_digest" != "$subject_digest"' in step_text
    assert "printf 'subject_digest=%s\\n' \"$subject_digest\"" in step_text
    assert "printf 'subject_digest=%s\\n' \"$remote_digest\"" not in step_text
    assert 'docker image inspect "$image"' in step_text
    assert "repo_digest" in step_text
    assert "sitecustomize.py" in step_text
    assert "pythonpath=/hostile" in step_text
    assert "sitecustomize-ran" in step_text


def test_candidate_job_verifies_provenance_and_exports_exact_handoff() -> None:
    document = _load_workflow()
    steps = document["jobs"]["publisher-image"]["steps"]

    provenance = next(
        step
        for step in steps
        if step.get("name") == "Attest candidate image provenance"
    )
    assert provenance["id"] == "provenance"
    assert_pinned_action(provenance["uses"], "actions/attest-build-provenance")

    handoff = next(
        step
        for step in steps
        if step.get("name") == "Verify provenance and write candidate handoff"
    )
    command = handoff["run"].lower()
    assert 'gh attestation verify "oci://$repo_digest"' in command
    assert "publisher-image-candidate.json" in command
    assert "dockerfile_sha256" in command
    assert "runtime_sha256" in command
    assert "base64.b64decode" in command
    assert "canonical_statement" in command
    assert "hashlib.sha256(canonical_statement)" in command
    assert "canonical_bundle" not in command
    assert handoff["env"]["ATTESTATION_ID"] == (
        "${{ steps.provenance.outputs.attestation-id }}"
    )

    upload = next(
        step for step in steps if step.get("name") == "Upload candidate handoff"
    )
    assert_pinned_action(upload["uses"], "actions/upload-artifact")
    assert upload["with"]["path"] == "publisher-image-candidate.json"
    assert upload["with"]["if-no-files-found"] == "error"


_BETA_TAG = "v1.0.0-beta.1"
_GH_STUB = f"""#!/usr/bin/env bash
case "$1 $2" in
  "release list") echo "{_BETA_TAG}" ;;
  "release view") echo "bsr-action-registry.json" ;;
  *) echo "gh stub: refused $*" >&2; exit 97 ;;
esac
"""


def _run_freshness_route(tmp_path: Path, changed: list[str]) -> str:
    """
    Run the real freshness-inspect step over a two-commit repository.

    The base commit carries the newest beta tag; the head commit edits
    ``changed``. ``gh`` is a stub that names that tag as the newest beta with a
    registry asset and refuses the registry download, so the step stops right
    after it has picked the drift route. Returns the combined output.
    """
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to run the workflow step")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    git("config", "user.name", "route test")
    git("config", "user.email", "route-test@example.invalid")
    git("config", "commit.gpgsign", "false")
    for path in changed:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("base\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("tag", _BETA_TAG)
    git("remote", "add", "origin", str(repo))
    for path in changed:
        (repo / path).write_text("head\n", encoding="utf-8")
    git("commit", "-q", "-am", "head")

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text(_GH_STUB, encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    inspect = next(
        step
        for step in _load_workflow()["jobs"]["inspect"]["steps"]
        if step.get("id") == "inspect"
    )
    env = {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "WARN_DAYS": "14",
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "GITHUB_REPOSITORY": "owner/repo",
    }
    completed = subprocess.run(  # noqa: S603
        [bash, "-c", inspect["run"]],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout + completed.stderr


def test_publisher_drift_refreshes_even_with_unlisted_paths(tmp_path: Path) -> None:
    # The 2026-11-16 beta dispatch: publisher-bound files moved alongside
    # harness files the route had no entry for. A refresh rebuilds from HEAD,
    # so no publisher equivalence is inferred and the unlisted paths must not
    # block the release.
    output = _run_freshness_route(
        tmp_path,
        [
            "AGENTS.md",
            ".pre-commit-config.yaml",
            "scripts/main_integrity.py",
            "publish-action/Dockerfile",
            "src/semantic_release/bsr/registry.py",
        ],
    )
    assert "unclassified source drift" not in output
    assert "publisher-bound source moved" in output


def test_unlisted_drift_without_publisher_change_still_fails_closed(
    tmp_path: Path,
) -> None:
    # With no publisher-bound change the route would keep the signed registry,
    # which means inferring equivalence: an unlisted path must stop it.
    output = _run_freshness_route(tmp_path, ["AGENTS.md"])
    assert "unclassified source drift" in output
    assert "publisher-bound source moved" not in output


def test_workflow_validator_pin_supports_attestations() -> None:
    content = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    match = re.search(
        r"python-jsonschema/check-jsonschema\s+rev:\s*[\"']?v?(\d+)\.(\d+)\.(\d+)",
        content,
    )
    assert match, "check-jsonschema must stay pinned in pre-commit"
    # 0.30.0 is the first release whose workflow schema accepts attestations.
    assert tuple(map(int, match.groups())) >= (0, 30, 0)


def test_pinned_non_root_image_runtime() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:3.13-slim-bookworm@sha256:" in content
    assert "ARG PUBLISHER_UID=" in content
    assert "ARG PUBLISHER_GID=" in content
    assert "ENV SOURCE_DATE_EPOCH=0" in content
    assert "USER publisher" in content
    assert (
        'ENTRYPOINT ["/usr/local/bin/python", "-I", "-S", "-B", "-u", "/opt/publisher/main.py"]'
        in content
    )
    assert "PYTHONPATH" not in content
    assert "pip install" not in content
