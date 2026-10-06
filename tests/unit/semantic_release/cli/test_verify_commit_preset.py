"""
Wiring tests for the --commit-preset rows in `bsr verify` (wave 2, W2.3).

Drives ``_commit_preset_checks`` against a scratch git repo so the CLI
option's full path (spec -> preset -> repo range -> CheckResult rows) is
executed, not just the module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from semantic_release.bsr.doctor import (
    SEVERITY_BLOCKER,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
)
from semantic_release.cli.commands.verify import _commit_preset_checks


def _git_repo(tmp_path: Path, subjects: tuple[str, ...]) -> Path:
    env_g = ["-c", "user.email=t@e.st", "-c", "user.name=T"]

    def run(*args: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argv, tmp_path cwd
            ["git", *env_g, *args],  # noqa: S607 - fixed binary name
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    run("init", "-q")
    for index, subject in enumerate(subjects):
        (tmp_path / "f.txt").write_text(f"{index}\n", encoding="utf-8")
        run("add", ".")
        run("commit", "-q", "-m", subject)
    return tmp_path


class TestWiring:
    def test_reject_mode_reports_blockers(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path, ("feat: good", "not conventional"))
        rows = _commit_preset_checks("conventional", repo)
        assert len(rows) == 2  # summary + 1 violation row (no truncation row)
        summary = rows[0]
        assert summary.status == STATUS_FAIL
        assert summary.severity == SEVERITY_BLOCKER
        assert "1/2 commits" in summary.what
        violation = rows[1]
        assert violation.status == STATUS_FAIL
        assert "not conventional" in violation.what

    def test_warn_mode_downgrades_severity(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path, ("feat: good", "not conventional"))
        rows = _commit_preset_checks("conventional:warn", repo)
        assert all(row.severity == SEVERITY_WARNING for row in rows)

    def test_parse_mode_is_observational_pass(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path, ("feat: good", "not conventional"))
        rows = _commit_preset_checks("conventional:parse", repo)
        assert len(rows) == 1
        assert rows[0].status == STATUS_PASS
        assert rows[0].severity == SEVERITY_INFO
        assert "1 non-conforming" in rows[0].what

    def test_clean_range_passes_in_reject_mode(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path, ("feat: good", "fix: also good"))
        rows = _commit_preset_checks("conventional", repo)
        assert len(rows) == 1
        assert rows[0].status == STATUS_PASS
        assert rows[0].severity == SEVERITY_BLOCKER

    def test_unresolvable_spec_fails_closed(self, tmp_path: Path) -> None:
        rows = _commit_preset_checks("no-such-preset", tmp_path)
        assert len(rows) == 1
        assert rows[0].status == STATUS_FAIL
        assert rows[0].severity == SEVERITY_BLOCKER
        assert "could not be loaded" in rows[0].what

    def test_gitmoji_preset_resolves_through_cli_spec(self, tmp_path: Path) -> None:
        repo = _git_repo(tmp_path, (":sparkles: shiny", "broken"))
        rows = _commit_preset_checks("gitmoji-map", repo)
        assert rows[0].status == STATUS_FAIL
        assert rows[0].what.startswith("1/2 commits")


@pytest.mark.parametrize(
    "spec", ["conventional", "conventional:warn", "conventional:parse"]
)
def test_real_repo_smoke(spec: str) -> None:
    """The wiring works against this actual worktree (has commits, no tag)."""
    repo_root = Path(__file__).resolve().parents[4]
    rows = _commit_preset_checks(spec, repo_root)
    assert rows
    assert all(row.code == "COMMIT_PRESET" for row in rows)
