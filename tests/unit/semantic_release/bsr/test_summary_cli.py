"""
CLI integration for `bsr.summary` (C3): exercises the real `# BSR-PATCH` seam
in `cli/commands/version.py` -- not `bsr/summary.py` in isolation (see
test_summary.py for that).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("t", "t@t")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pyproject_toml(extra_bsr: str) -> str:
    return (
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'tag_format = "v{version}"\n'
        # NOTE: same reasoning as test_path_filter_cli.py -- PSR forces a
        # MAJOR bump out of 0.x.x whenever allow_zero_version is False,
        # regardless of which commits matched, which would make the "api"
        # component's no-release assertion below false.
        "allow_zero_version = true\n" + extra_bsr
    )


def _build_monorepo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_bsr: str = ""
) -> Path:
    """
    A 2-component monorepo (`apps/api`, `apps/web`) released once at `v0.1.0`
    (both paths touched), then ONE new `feat` commit touching ONLY
    `apps/web`.
    """
    proj = tmp_path / "proj"
    _write(proj / "pyproject.toml", _pyproject_toml(extra_bsr))
    _write(proj / "apps" / "api" / "x.py", "print('api')\n")
    _write(proj / "apps" / "web" / "y.py", "print('web')\n")
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml", "apps/api/x.py", "apps/web/y.py"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")

    _write(proj / "apps" / "web" / "y.py", "print('web v2')\n")
    repo.index.add(["apps/web/y.py"])
    repo.index.commit("feat: update web page", author=_AUTHOR, committer=_AUTHOR)

    monkeypatch.chdir(proj)
    return proj


def _invoke_noop() -> object:
    return CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )


_COMPONENTS_TOML = (
    "\n[[tool.semantic_release.bsr.components]]\n"
    'name = "api"\n'
    'paths = ["apps/api"]\n'
    "[[tool.semantic_release.bsr.components]]\n"
    'name = "web"\n'
    'paths = ["apps/web"]\n'
)


def test_summary_off_by_default_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_monorepo(tmp_path, monkeypatch)
    result = _invoke_noop()
    assert result.exit_code == 0
    assert "release plan" not in result.output
    assert "would-release" not in result.output


def test_summary_on_with_components_renders_per_component_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_monorepo(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nsummary = true\n" + _COMPONENTS_TOML,
    )
    result = _invoke_noop()
    assert result.exit_code == 0
    assert "better-semantic-release summary: monorepo release plan" in result.output
    assert "api" in result.output
    assert "web" in result.output
    # api: no commit touched apps/api since v0.1.0 -> no release
    api_line = next(line for line in result.output.splitlines() if line.strip().startswith("api"))
    assert "no" in api_line
    assert "0" in api_line
    # web: one feat commit touched apps/web -> MINOR release to 0.2.0
    web_line = next(line for line in result.output.splitlines() if line.strip().startswith("web"))
    assert "yes" in web_line
    assert "MINOR" in web_line
    assert "1" in web_line
    assert "0.2.0" in web_line


def test_summary_on_without_components_falls_back_to_single_repo_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `bsr.components` configured -- falls back to one row for the whole repo."""
    _build_monorepo(
        tmp_path, monkeypatch, extra_bsr="\n[tool.semantic_release.bsr]\nsummary = true\n"
    )
    result = _invoke_noop()
    assert result.exit_code == 0
    assert "better-semantic-release summary: monorepo release plan" in result.output
    assert "demo" in result.output  # falls back to the project name


def test_summary_stdout_untouched_when_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The summary table is stderr-only -- stdout still prints ONLY the version."""
    _build_monorepo(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nsummary = true\n" + _COMPONENTS_TOML,
    )
    result = CliRunner(mix_stderr=False).invoke(
        main, ["--noop", "version", "--print"]
    )
    assert result.exit_code == 0
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?\n", result.stdout)
    assert "release plan" not in result.stdout
    assert "release plan" in result.stderr
