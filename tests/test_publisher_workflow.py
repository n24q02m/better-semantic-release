from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cd.yml"
DOCKERFILE = ROOT / "publish-action" / "Dockerfile"

def _load_workflow() -> dict[str, object]:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_manual_cd_has_mutually_exclusive_release_and_image_operations() -> None:
    document = _load_workflow()

    dispatch = document["on"]["workflow_dispatch"]
    operation = dispatch["inputs"]["operation"]
    assert operation["default"] == "release"
    assert operation["options"] == ["release", "publisher-image"]
    assert document["jobs"]["release"]["if"] == "inputs.operation == 'release'"
    image = document["jobs"]["publisher-image"]
    assert image["if"] == (
        "inputs.operation == 'publisher-image' && "
        "github.ref == 'refs/heads/main'"
    )
    assert image["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert image["environment"]["name"] == "beta-publish"


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
    workflow_text = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "gh release" not in workflow_text


def test_publisher_runtime_is_not_the_final_action_yet() -> None:
    assert not (ROOT / "publish-action" / "action.yml").exists()


def test_candidate_job_contains_only_image_bootstrap_mutations() -> None:
    document = _load_workflow()
    image = document["jobs"]["publisher-image"]
    step_text = "\n".join(
        f"{step.get('name', '')} {step.get('run', '')} {step.get('uses', '')}"
        for step in image["steps"]
    ).lower()
    assert "pypi" not in step_text
    assert "gh release" not in step_text
    assert "semantic-release publish" not in step_text


def test_pinned_non_root_image_runtime() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:3.13-slim-bookworm@sha256:" in content
    assert "USER publisher" in content
    assert 'ENTRYPOINT ["python", "/opt/publisher/main.py"]' in content
    assert "pip install" not in content
