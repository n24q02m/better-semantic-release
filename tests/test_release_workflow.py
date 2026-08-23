"""Static policy tests for the dispatch-only release workflow."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "cd.yml"


def test_release_job_is_bound_to_channel_environment() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    release_job = document["jobs"]["release"]

    assert release_job["environment"]["name"] == (
        "${{ inputs.release_type == 'beta' && 'beta-publish' || 'stable-publish' }}"
    )
