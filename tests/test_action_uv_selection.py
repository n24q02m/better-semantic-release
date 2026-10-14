"""Static policy tests for how the composite action selects the uv version.

The action runs ``semantic-release`` inside ``inputs.directory`` and that
directory's own ``uv.toml`` / ``pyproject.toml`` may carry a
``required-version`` range. ``astral-sh/setup-uv`` only reads that range from its
``working-directory``, so the install step must point there. Otherwise it reads
the workspace root, finds nothing, installs the newest uv, and any consumer whose
build command runs ``uv`` fails with ``Required uv version ... does not match``.

The behavior itself (a real uv install picking the right version) is exercised by
the ``action-uv-version-selection`` job in ``ci.yml``; these tests pin the wiring
so it cannot regress without that job noticing.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION = Path(__file__).parents[1] / "action.yml"
SETUP_UV_PREFIX = "astral-sh/setup-uv@"


def _setup_uv_step() -> dict[str, object]:
    document = yaml.load(  # noqa: S506 - BaseLoader never constructs objects
        ACTION.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    steps = document["runs"]["steps"]
    matches = [s for s in steps if str(s.get("uses", "")).startswith(SETUP_UV_PREFIX)]
    assert len(matches) == 1, "expected exactly one setup-uv step in the action"
    return matches[0]


def test_setup_uv_reads_version_from_the_release_directory() -> None:
    step = _setup_uv_step()

    assert step["with"]["working-directory"] == (
        "${{ github.workspace }}/${{ inputs.directory }}"
    )


def test_setup_uv_does_not_pin_a_version_or_python() -> None:
    # A pinned `version` would override the consumer's required-version range;
    # `version-file` would make setup-uv throw when the file has no such key;
    # `python-version` exports UV_PYTHON and shadows the action's own
    # `uv venv --python 3.13`.
    with_block = _setup_uv_step().get("with", {})

    assert not {"version", "version-file", "python-version"} & set(with_block)
