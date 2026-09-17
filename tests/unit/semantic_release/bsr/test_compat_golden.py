"""Golden tests locking the legacy behavior contracts.

These tests exist so that the universal-release-engine work (ReleasePlan, the
``plan``/``verify``/``doctor`` commands, publisher adapters) cannot silently
break the drop-in promise documented in the README "Compatibility contract"
section. Each test pins one legacy surface:

* the GitHub Action output contract (names + step passthrough wiring);
* the legacy config schema parsing without any ``[tool.semantic_release.bsr]``
  table (the drop-in default path).

The bare-stdout contract of ``semantic-release version --print`` is pinned by
``tests/e2e/cmd_version/test_version_print.py``
(``assert f"{version}\n" == result.stdout``); it is intentionally not duplicated
here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from semantic_release.bsr.config import BsrConfig, load_bsr_config

TESTS_DIR = Path(__file__).parents[3]
ACTION_YML = TESTS_DIR.parent / "action.yml"

# The action contract as it exists today. Renaming or removing any of these
# outputs is a breaking change for every consumer of the action and must fail
# this test loudly.
LEGACY_ACTION_OUTPUTS = (
    "commit_sha",
    "is_prerelease",
    "link",
    "previous_version",
    "released",
    "release_notes",
    "tag",
    "version",
)


def test_action_yml_output_contract() -> None:
    """Every legacy output keeps its name and its step passthrough wiring."""
    action = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    outputs = action["outputs"]

    missing = [name for name in LEGACY_ACTION_OUTPUTS if name not in outputs]
    assert not missing, f"action outputs were removed/renamed: {missing}"

    for name in LEGACY_ACTION_OUTPUTS:
        expected = f"${{{{ steps.run.outputs.{name} }}}}"
        actual = outputs[name]["value"]
        assert actual == expected, (
            f"output {name!r} wiring changed: expected {expected!r}, got {actual!r}"
        )


def test_legacy_config_without_bsr_table_loads_with_defaults(tmp_path: Path) -> None:
    """A legacy pyproject.toml (no ``[tool.semantic_release.bsr]``) still parses.

    The parsed BSR config must be the all-defaults instance -- i.e. every BSR
    feature stays opt-in and no new required key can be introduced at the top
    level of ``[tool.semantic_release]``.
    """
    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        '[tool.semantic_release]\ntag_format = "v{version}"\n',
        encoding="utf-8",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(
        guard_orphan_tag=True,
        guard_registry_collision=True,
        registry="",
        path_filter=False,
        paths=(),
        explain=False,
        actionable_errors=False,
    )
