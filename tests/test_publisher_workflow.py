from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cd.yml"
DOCKERFILE = ROOT / "publish-action" / "Dockerfile"
PUBLISH_ACTION = ROOT / "publish-action" / "action.yml"
REGISTRY_ACTION = ROOT / "registry-action" / "action.yml"
PUBLISHER_DIGEST = (
    "sha256:6be3e4d7b610f0c946f0fd237cf7d74c70a1f2be343dd038d731b8fe405151ab"
)


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
    assert operation["options"] == ["release", "publisher-image", "registry-g1"]
    for input_name in ("registry_tag", "candidate_run_id", "candidate_run_attempt"):
        assert dispatch["inputs"][input_name]["required"] == "false"

    assert document["jobs"]["release"]["if"] == "inputs.operation == 'release'"
    image = document["jobs"]["publisher-image"]
    assert image["if"] == (
        "inputs.operation == 'publisher-image' && " "github.ref == 'refs/heads/main'"
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
    assert buildx["uses"] == (
        "docker/setup-buildx-action@" "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f"
    )

    registry = document["jobs"]["registry-g1"]
    assert registry["if"] == (
        "inputs.operation == 'registry-g1' && " "github.ref == 'refs/heads/main'"
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
    assert release["if"] == "inputs.operation == 'release'"
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
        "docker://ghcr.io/n24q02m/better-semantic-release-publisher@"
        f"{PUBLISHER_DIGEST}"
    )
    assert action["runs"]["args"] == ["--manifest", "${{ inputs.manifest }}"]


def test_registry_action_is_a_self_contained_remote_loader() -> None:
    action = yaml.safe_load(REGISTRY_ACTION.read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    assert (
        action["outputs"]["registry"]["value"] == "${{ steps.load.outputs.registry }}"
    )
    installer, loader = action["runs"]["steps"]
    assert installer["uses"] == (
        "sigstore/cosign-installer@" "6f9f17788090df1f26f669e9d70d6ae9567deba6"
    )
    assert loader["id"] == "load"
    command = loader["run"]
    assert "scripts/action_pin_registry.py" in command
    assert 'action_pin_registry.py" load' in command
    assert "--expected" in command
    assert "--repository" in command


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
    upload = next(
        step
        for step in registry["steps"]
        if step.get("name") == "Upload signed G1 evidence"
    )
    assert upload["uses"] == (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    assert upload["with"]["if-no-files-found"] == "error"


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
    assert provenance["uses"] == (
        "actions/attest-build-provenance@" "e8998f949152b193b063cb0ec769d69d929409be"
    )

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
    assert handoff["env"]["ATTESTATION_ID"] == (
        "${{ steps.provenance.outputs.attestation-id }}"
    )

    upload = next(
        step for step in steps if step.get("name") == "Upload candidate handoff"
    )
    assert upload["uses"] == (
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
    )
    assert upload["with"]["path"] == "publisher-image-candidate.json"
    assert upload["with"]["if-no-files-found"] == "error"


def test_workflow_validator_pin_supports_attestations() -> None:
    content = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert 'rev: "0.30.0"' in content


def test_pinned_non_root_image_runtime() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:3.13-slim-bookworm@sha256:" in content
    assert "ARG PUBLISHER_UID=" in content
    assert "ARG PUBLISHER_GID=" in content
    assert "USER publisher" in content
    assert (
        'ENTRYPOINT ["/usr/local/bin/python", "-I", "-S", "-B", "-u", "/opt/publisher/main.py"]'
        in content
    )
    assert "PYTHONPATH" not in content
    assert "pip install" not in content
