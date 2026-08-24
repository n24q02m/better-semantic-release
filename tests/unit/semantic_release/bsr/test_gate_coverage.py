"""Hold every tracked-upstream difference to the ownership manifest and gates."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
import yaml
from scripts.check_upstream_ownership import (
    COVERAGE_GATES,
    PYTHON_GATES,
    load_manifest,
    scope_for_gate,
    validate_ownership,
)

LINT_GATES = PYTHON_GATES[:3]


REPO_ROOT = Path(__file__).resolve().parents[4]
MARKER = "BSR-PATCH"


def _ci_steps() -> list[dict]:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    return workflow["jobs"]["lint-type-test"]["steps"]


def _mise_lint_commands() -> list[str]:
    mise = tomlkit.parse((REPO_ROOT / ".mise.toml").read_text(encoding="utf-8"))
    return [str(command) for command in mise["tasks"]["lint"]["run"]]


def _marked_sources() -> list[str]:
    marked = [
        path
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
        if MARKER in path.read_text(encoding="utf-8")
    ]
    return [path.relative_to(REPO_ROOT).as_posix() for path in marked]


def test_manifest_covers_the_complete_tracked_upstream_difference() -> None:
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")

    assert validate_ownership(manifest).errors == ()


def test_every_marked_stock_file_has_explicit_manifest_ownership() -> None:
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")
    owned = {entry.path for entry in manifest.entries}

    assert _marked_sources()
    assert set(_marked_sources()) <= owned


@pytest.mark.parametrize("gate", COVERAGE_GATES)
def test_manifest_reports_separate_eligible_and_covered_counts(gate: str) -> None:
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")
    result = validate_ownership(manifest)

    counts = result.coverage[gate]
    assert counts.eligible > 0
    assert counts.covered == counts.eligible


@pytest.mark.parametrize("gate", PYTHON_GATES)
def test_ci_runs_the_manifest_derived_python_gate(gate: str) -> None:
    expected = f"scripts/check_upstream_ownership.py --run-gate {gate}"
    commands = [step.get("run", "") for step in _ci_steps()]

    assert any(expected in command for command in commands)
    assert scope_for_gate(
        load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml"), gate
    )


@pytest.mark.parametrize("gate", LINT_GATES)
def test_mise_lint_runs_the_same_manifest_derived_python_gate(gate: str) -> None:
    expected = f"scripts/check_upstream_ownership.py --run-gate {gate}"

    assert any(expected in command for command in _mise_lint_commands())


def test_mypy_config_covers_manifest_type_scope() -> None:
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")
    pyproject = tomlkit.parse(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    targets = [str(target) for target in pyproject["tool"]["mypy"]["files"]]

    assert all(
        any(
            path == target or path.startswith(f"{target.rstrip('/')}/")
            for target in targets
        )
        for path in scope_for_gate(manifest, "python-type")
    )
