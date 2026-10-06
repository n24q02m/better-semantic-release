"""Static policy tests for the dispatch-only release workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "cd.yml"


def _load_workflow() -> dict[str, object]:
    # BaseLoader keeps the `on:` key as a string instead of the YAML 1.1 boolean.
    return yaml.load(
        WORKFLOW.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,  # noqa: S506
    )


def test_release_job_is_bound_to_channel_environment() -> None:
    document = _load_workflow()
    release_job = document["jobs"]["release"]

    assert release_job["environment"]["name"] == (
        "${{ inputs.release_type == 'beta' && 'beta-publish' || 'stable-publish' }}"
    )


def test_release_is_dispatch_only() -> None:
    document = _load_workflow()
    triggers = document["on"]

    assert "push" not in triggers
    assert "pull_request" not in triggers
    assert document["env"]["RELEASE_TYPE"] == "${{ inputs.release_type }}"
    assert (
        document["jobs"]["release"]["if"]
        == "inputs.operation == 'release' && (inputs.parent_run_id != '' || needs.inspect.outputs.refresh != 'true')"
    )
    assert document["jobs"]["release"]["needs"] == ["inspect"]

    release_type = triggers["workflow_dispatch"]["inputs"]["release_type"]
    assert release_type["type"] == "choice"
    assert release_type["required"] == "true"
    assert release_type["options"] == ["beta", "stable"]
    assert "default" not in release_type

    assert "event_name == 'push'" not in WORKFLOW.read_text(encoding="utf-8")
