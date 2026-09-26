r"""
Golden tests locking the legacy behavior contracts.

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
from typing import TYPE_CHECKING

import pytest
import yaml
from git import Actor, Repo

from semantic_release.cli.commands.main import main

from tests.conftest import get_cli_runner

if TYPE_CHECKING:
    import pytest

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
        assert (
            actual == expected
        ), f"output {name!r} wiring changed: expected {expected!r}, got {actual!r}"


# The W1.1 additive surface: mode input + plan outputs. Additive only -- this
# test locks that the new surface exists as declared and that the default
# behavior input cannot silently flip away from "version" (fleet pins @v1).
ADDED_PLAN_OUTPUTS = ("plan_blocked", "plan_json")


def test_action_yml_additive_plan_surface() -> None:
    """The plan/verify surface is additive: mode defaults to version, new outputs are wired."""
    action = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))

    # New input with the legacy default
    mode = action["inputs"].get("mode")
    assert mode is not None, "mode input was removed"
    assert mode["default"] == "version", "mode default must stay 'version'"

    # New outputs exist and pass through the run step
    for name in ADDED_PLAN_OUTPUTS:
        expected = f"${{{{ steps.run.outputs.{name} }}}}"
        actual = action["outputs"].get(name, {}).get("value")
        assert (
            actual == expected
        ), f"output {name!r} wiring changed: expected {expected!r}, got {actual!r}"

    # The run step actually receives the mode input
    run_step = next(step for step in action["runs"]["steps"] if step.get("id") == "run")
    assert "INPUT_MODE" in run_step["env"], "run step env lost INPUT_MODE"


def test_legacy_config_without_bsr_table_loads_with_defaults(tmp_path: Path) -> None:
    """
    A legacy pyproject.toml (no ``[tool.semantic_release.bsr]``) still parses.

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


# ---------------------------------------------------------------------------
# W1.7: golden stdout of `version` (machine surface) and the default
# `changelog` output on a real fixture — the drop-in behavior contract, not
# just the action.yml wiring pinned above.
#
# The fixture pins author/commit DATES so the pending commit sha (and thus
# the changelog links and release dates) is reproducible; the changelog
# golden parameterizes only that sha (%SHORT_SHA%/%FULL_SHA%).

_FIXTURE_DATE = "2024-01-15T12:00:00 +0000"
_FIXTURE_AUTHOR = Actor("demo", "demo@example.com")

_DEFAULT_BSR_TABLE = "[tool.semantic_release.bsr]\nschema_version = 1\n"


_CLI_ENV = {
    # hvcs/github.py prefers GITHUB_REPOSITORY over the remote URL for
    # commit links; pin it so dev and CI render identical changelog URLs.
    "GITHUB_TOKEN": "test-token",
    "GITHUB_REPOSITORY": "example-owner/example-repo",
}


def _build_release_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str = "proj",
    bsr_table: str = "",
) -> Path:
    """A repo released once at `v0.1.0` with one pending feat commit."""
    proj = tmp_path / name
    (proj / "src" / "demo").mkdir(parents=True)
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n" + bsr_table,
        encoding="utf-8",
    )
    (proj / "src" / "demo" / "__init__.py").write_text(
        "__version__ = '0.1.0'\n", encoding="utf-8"
    )
    repo = Repo.init(proj, initial_branch="main")
    repo.index.add(["pyproject.toml", "src/demo/__init__.py"])
    repo.index.commit(
        "feat: initial release",
        author=_FIXTURE_AUTHOR,
        committer=_FIXTURE_AUTHOR,
        author_date=_FIXTURE_DATE,
        commit_date=_FIXTURE_DATE,
    )
    repo.create_tag("v0.1.0")
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    (proj / "src" / "demo" / "__init__.py").write_text(
        "__version__ = '0.2.0'\n", encoding="utf-8"
    )
    repo.index.add(["src/demo/__init__.py"])
    repo.index.commit(
        "feat: add greeting",
        author=_FIXTURE_AUTHOR,
        committer=_FIXTURE_AUTHOR,
        author_date=_FIXTURE_DATE,
        commit_date=_FIXTURE_DATE,
    )
    monkeypatch.chdir(proj)
    return proj


_GOLDEN_VERSION_JSON_LINES = [
    "{",
    '  "schema_version": 1,',
    '  "released": true,',
    '  "version": "0.2.0",',
    '  "tag": "v0.2.0",',
    '  "is_prerelease": false,',
    '  "previous_version": "0.1.0",',
    '  "reason": null,',
    '  "commit_count": 1,',
    '  "level_bump": "minor",',
    '  "type_counts": {',
    '    "features": 1',
    "  },",
    '  "components": []',
    "}",
]


def test_version_json_stdout_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`version --format json` stdout is byte-exact on a legacy fixture."""
    _build_release_fixture(tmp_path, monkeypatch)
    result = get_cli_runner().invoke(
        main,
        ["--noop", "version", "--format", "json"],
        env={"GITHUB_TOKEN": "test-token"},
    )
    assert result.exit_code == 0
    assert str(result.stdout) == "\n".join(_GOLDEN_VERSION_JSON_LINES) + "\n"


_GOLDEN_CHANGELOG_LINES = [
    "# CHANGELOG",
    "",
    "<!-- version list -->",
    "",
    "## Unreleased",
    "",
    "### Features",
    "",
    "- Add greeting",
    "  ([`%SHORT_SHA%`](https://github.com/example-owner/example-repo/commit/%FULL_SHA%))",
    "",
    "",
    "## v0.1.0 (2024-01-15)",
    "",
    "- Initial Release",
]


def test_changelog_default_output_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default changelog template output is byte-exact on a legacy fixture."""
    proj = _build_release_fixture(tmp_path, monkeypatch)
    full_sha = str(Repo(str(proj)).head.commit.hexsha)
    result = get_cli_runner().invoke(
        main, ["changelog"], env=_CLI_ENV
    )
    assert result.exit_code == 0
    content = (proj / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    expected = "\n".join(_GOLDEN_CHANGELOG_LINES) + "\n"
    expected = expected.replace("%SHORT_SHA%", full_sha[:7]).replace(
        "%FULL_SHA%", full_sha
    )
    assert content == expected


def _version_json_and_changelog(proj: Path) -> tuple[str, str]:
    runner = get_cli_runner()
    json_result = runner.invoke(
        main,
        ["--noop", "version", "--format", "json"],
        env={"GITHUB_TOKEN": "test-token"},
    )
    assert json_result.exit_code == 0
    cl_result = runner.invoke(main, ["changelog"], env=_CLI_ENV)
    assert cl_result.exit_code == 0
    changelog = (
        (proj / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    )
    return str(json_result.stdout), changelog


def test_no_bsr_config_behavior_is_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Adding an all-default ``[tool.semantic_release.bsr]`` table changes nothing.

    The legacy path (no bsr table at all) and the bsr-defaults path must
    produce byte-identical `version` JSON stdout and byte-identical default
    changelog output, modulo the pending commit sha which differs by
    construction (the pyproject blob itself differs).
    """
    import re

    bare = _build_release_fixture(tmp_path, monkeypatch, name="bare")
    bare_json, bare_changelog = _version_json_and_changelog(bare)

    with_bsr = _build_release_fixture(
        tmp_path, monkeypatch, name="with_bsr", bsr_table=_DEFAULT_BSR_TABLE
    )
    bsr_json, bsr_changelog = _version_json_and_changelog(with_bsr)

    assert bare_json == bsr_json

    sha_re = re.compile(r"\b[0-9a-f]{7,40}\b")
    assert sha_re.sub("<SHA>", bare_changelog) == sha_re.sub("<SHA>", bsr_changelog)
