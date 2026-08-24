"""Regression tests for the tracked-upstream ownership manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_upstream_ownership import (
    COVERAGE_GATES,
    _gate_command,
    load_manifest,
    scope_for_gate,
    validate_ownership,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_unowned_changed_stock_file_fails_closed(tmp_path: Path) -> None:
    """A changed stock file without manifest ownership must fail the gate."""
    manifest_path = tmp_path / "ownership.toml"
    stock_path = tmp_path / "src" / "semantic_release" / "stock.py"
    stock_path.parent.mkdir(parents=True)
    stock_path.write_text("value = 1\n", encoding="utf-8")

    manifest_path.write_text('baseline = "test-baseline"\n', encoding="utf-8")

    manifest = load_manifest(manifest_path)
    result = validate_ownership(
        manifest,
        changed_files=("src/semantic_release/stock.py",),
    )

    assert result.errors == ("unowned changed files: src/semantic_release/stock.py",)


def test_manifest_status_mismatch_fails_closed(tmp_path: Path) -> None:
    """The manifest must match both changed paths and their Git statuses."""
    manifest_path = tmp_path / "ownership.toml"
    manifest_path.write_text(
        """
baseline = "test-baseline"

[[ownership]]
path = "src/semantic_release/stock.py"
status = "M"
kind = "python-source"
owner = "repo-maintainers"
reason = "Owned stock patch."
gates = ["python-lint", "python-format", "python-type"]
""".lstrip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)
    result = validate_ownership(
        manifest,
        changed_files={"src/semantic_release/stock.py": "D"},
    )

    assert result.errors == (
        "ownership status mismatch: src/semantic_release/stock.py "
        "(manifest=M, actual=D)",
    )


def test_manifest_derives_separate_gate_scopes_and_counts() -> None:
    """Each declared gate gets its own scope and eligible/covered count."""
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")
    result = validate_ownership(manifest)

    assert result.errors == ()
    assert set(result.coverage) == set(COVERAGE_GATES)
    assert all(
        counts.eligible == counts.covered and counts.eligible > 0
        for counts in result.coverage.values()
    )

    lint_scope = scope_for_gate(manifest, "python-lint")
    type_scope = scope_for_gate(manifest, "python-type")
    assert "src/semantic_release/bsr/config.py" in lint_scope
    assert "src/semantic_release/cli/commands/version.py" in type_scope
    assert not any(path.endswith(".rst") for path in lint_scope)


@pytest.mark.parametrize("gate", COVERAGE_GATES)
def test_manifest_gate_scope_is_non_empty(gate: str) -> None:
    """The manifest cannot silently declare an empty quality-gate scope."""
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")

    assert scope_for_gate(manifest, gate)


@pytest.mark.parametrize(
    "gate,tokens",
    [
        ("python-lint", ("ruff", "check")),
        ("python-format", ("ruff", "format", "--check")),
        ("python-type", ("mypy",)),
        ("python-test", ("pytest", "-q")),
    ],
)
def test_gate_command_uses_expected_engine_and_manifest_scope(
    gate: str, tokens: tuple[str, ...]
) -> None:
    """Every executable gate must use its engine and its manifest-derived scope."""
    manifest = load_manifest(REPO_ROOT / "config/bsr-upstream-ownership.toml")

    command = _gate_command(manifest, gate)

    assert all(token in command for token in tokens)
    assert set(scope_for_gate(manifest, gate)).issubset(command)
