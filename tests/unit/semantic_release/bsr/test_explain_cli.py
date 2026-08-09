from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("t", "t@t")


def _build_minimal_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_pyproject: str = ""
) -> Path:
    """
    Same fixture shape as test_version_hook.py's helper (see there for why
    the `origin` remote is required to reach the guard/explain seam).
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
        'tag_format = "v{version}"\n' + extra_pyproject,
        encoding="utf-8",
    )
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    monkeypatch.chdir(proj)
    return proj


def _add_chore_commit(proj: Path) -> None:
    (proj / "notes.txt").write_text("notes\n", encoding="utf-8")
    repo = Repo(str(proj))
    repo.index.add(["notes.txt"])
    repo.index.commit("chore: tidy up", author=_AUTHOR, committer=_AUTHOR)


def test_explain_off_by_default_keeps_stock_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Drop-in default: no [tool.semantic_release.bsr] table at all -> stock
    PSR's misattributed "already been released" wording, unchanged.
    """
    proj = _build_minimal_project(tmp_path, monkeypatch)
    _print = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert _print.exit_code == 0
    computed_version = _print.output.strip().splitlines()[-1]
    Repo(str(proj)).create_tag(f"v{computed_version}")
    _add_chore_commit(proj)

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "has already been released" in result.output
    assert "NO_QUALIFYING_COMMITS" not in result.output


def test_explain_on_reports_no_qualifying_commits_not_already_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The core C1 fix: a chore-only repo since the last release must report
    the CORRECT reason (NO_QUALIFYING_COMMITS, N commits scanned) instead of
    PSR's misattributed "has already been released!" wording.
    """
    proj = _build_minimal_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nexplain = true\n",
    )
    _print = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert _print.exit_code == 0
    computed_version = _print.output.strip().splitlines()[-1]
    Repo(str(proj)).create_tag(f"v{computed_version}")
    _add_chore_commit(proj)

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "NO_QUALIFYING_COMMITS" in result.output
    assert "1 commit(s) scanned" in result.output
    assert "has already been released" not in result.output


def test_explain_on_reports_already_released_noop_for_zero_new_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A genuine benign no-op (tip already tagged, zero new commits at all)
    classifies as ALREADY_RELEASED_NOOP, not NO_QUALIFYING_COMMITS.
    """
    proj = _build_minimal_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nexplain = true\n",
    )
    _print = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert _print.exit_code == 0
    computed_version = _print.output.strip().splitlines()[-1]
    Repo(str(proj)).create_tag(f"v{computed_version}")

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "ALREADY_RELEASED_NOOP" in result.output
    assert "NO_QUALIFYING_COMMITS" not in result.output


def test_explain_on_prints_why_this_bump_for_a_real_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_minimal_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nexplain = true\n",
    )
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "better-semantic-release explain:" in result.output
    assert "bump" in result.output


def test_explain_off_prints_no_why_this_bump_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_minimal_project(tmp_path, monkeypatch)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "better-semantic-release explain:" not in result.output


def test_explain_strict_mode_also_gets_classified_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_minimal_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nexplain = true\n",
    )
    _print = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert _print.exit_code == 0
    computed_version = _print.output.strip().splitlines()[-1]
    Repo(str(proj)).create_tag(f"v{computed_version}")
    _add_chore_commit(proj)

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--strict", "--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 2
    assert "NO_QUALIFYING_COMMITS" in result.output


def test_explain_on_classifies_orphan_when_guard_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With guard_orphan_tag=false (opted out), a genuine orphan/rewritten-tag
    silent-freeze no longer escalates -- but with explain=true it must still
    be classified honestly as ORPHAN, not misreported as a benign no-op.
    """
    proj = _build_minimal_project(
        tmp_path,
        monkeypatch,
        extra_pyproject=(
            "\n[tool.semantic_release.bsr]\nexplain = true\nguard_orphan_tag = false\n"
        ),
    )
    repo = Repo(str(proj))

    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    v1 = printed.output.strip().splitlines()[-1]
    repo.create_tag(f"v{v1}")
    c1 = repo.head.commit

    (proj / "feature2.txt").write_text("feature 2\n", encoding="utf-8")
    repo.index.add(["feature2.txt"])
    repo.index.commit("feat: add thing 2", author=_AUTHOR, committer=_AUTHOR)
    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    v2 = printed.output.strip().splitlines()[-1]
    repo.create_tag(f"v{v2}")

    repo.git.reset("--hard", c1.hexsha)
    (proj / "feature3.txt").write_text("feature 3\n", encoding="utf-8")
    repo.index.add(["feature3.txt"])
    repo.index.commit("feat: add thing 3", author=_AUTHOR, committer=_AUTHOR)
    assert not repo.is_ancestor(repo.tags[f"v{v2}"].commit, repo.head.commit)

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0  # guard opted out: stays silent (exit 0), like stock
    assert "ORPHAN" in result.output
    assert "SILENT RELEASE FREEZE PREVENTED" not in result.output
