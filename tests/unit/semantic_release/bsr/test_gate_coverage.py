"""
Every file this fork has patched must sit behind this fork's quality gates.

The gates -- the ruff path list in ci.yml and ``[tool.mypy] files`` in
pyproject.toml -- name a stock tree's files one by one, because checking all of
python-semantic-release would fail on code this fork does not own. That design is
right, but it is maintained by hand, and a hand-maintained list is only as good
as whoever last remembered it. ``cli/commands/publish.py`` was patched and left
out of both lists; so were ``changelog/release_history.py`` and
``cli/github_actions_output.py``. Three misses is a pattern, not an accident.

``# BSR-PATCH:`` already marks the lines this fork owns inside otherwise-stock
files, so the answer to "what did we patch?" is in the source, not in anyone's
memory. This test reads it from there and holds the two lists to it. Adding a
patch to a new file now fails here until the file is gated, which is the point:
the check that catches the omission should be the one that runs on every commit,
not the next audit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
MARKER = "BSR-PATCH"


def _patched_sources() -> list[str]:
    """Repo-relative posix paths of the stock files this fork has patched."""
    marked = [
        path
        for path in sorted((REPO_ROOT / "src").rglob("*.py"))
        if MARKER in path.read_text(encoding="utf-8")
    ]
    return [path.relative_to(REPO_ROOT).as_posix() for path in marked]


def _ci_steps() -> list[dict]:
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    return workflow["jobs"]["lint-type-test"]["steps"]


def _ruff_targets(command: str) -> list[str]:
    """
    The paths a ruff step is pointed at, read out of the command it runs.

    Reading the command rather than a copy of the list kept somewhere else is
    deliberate: a second copy is one more thing that can drift, and drift is the
    failure this whole test exists to catch.

    Matched on the whole `ruff <subcommand>` fragment, not the subcommand alone:
    "check" on its own also matches the `--check` flag of the format step, which
    is how this picked up two steps for one gate the first time it ran.
    """
    runs = [step["run"] for step in _ci_steps() if command in step.get("run", "")]
    assert len(runs) == 1, f"expected exactly one `{command}` step, got {runs}"
    return [
        token
        for token in runs[0].split()
        if not token.startswith("-") and (REPO_ROOT / token).exists() and "/" in token
    ]


def _mypy_targets() -> list[str]:
    pyproject = tomlkit.parse(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return [str(entry) for entry in pyproject["tool"]["mypy"]["files"]]


def _is_covered(source: str, targets: list[str]) -> bool:
    """A target covers a file by naming it, or by naming a directory above it."""
    return any(
        source == target or source.startswith(f"{target.rstrip('/')}/")
        for target in targets
    )


# Read lazily, inside the tests. Resolving these at import time turns one missing
# CI step into a collection error, which takes the other two gates down with it and
# reports the problem as "cannot collect" rather than as the gate that is missing.
GATES = {
    "ruff check": lambda: _ruff_targets("ruff check"),
    "ruff format --check": lambda: _ruff_targets("ruff format --check"),
    "mypy": _mypy_targets,
}


@pytest.mark.parametrize("gate", GATES)
def test_the_gate_reads_a_real_list(gate: str):
    """
    Guards the test below. If the extraction silently returned nothing, every
    file would look ungated and the failure would point at the wrong thing.
    """
    assert GATES[gate](), f"read no targets at all for {gate} -- extraction is broken"


@pytest.mark.parametrize("gate", GATES)
def test_every_patched_file_is_gated(gate: str):
    patched = _patched_sources()
    assert patched, f"found no {MARKER} markers under src/ -- the scan is broken"

    targets = GATES[gate]()
    ungated = [source for source in patched if not _is_covered(source, targets)]
    assert not ungated, (
        f"these files carry {MARKER} markers but {gate} never looks at them: "
        f"{ungated}. Add them to the list, or drop the patch."
    )
