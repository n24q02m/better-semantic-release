"""
Drop-in parity proof for `bsr.summary` (C3).

`summary = false` explicit must be byte-identical to no `[tool.
semantic_release.bsr]` table at all -- same stdout, same stderr, same exit
code. A separate stdout-isolation check (mix_stderr=False) additionally
proves stdout carries ONLY the version -- the hard "STDOUT is a machine
contract" constraint -- for both `summary = false` AND `summary = true`.
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
_TIMESTAMP_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}\]")


def _normalized(output: str) -> str:
    """Strip RichHandler's `[HH:MM:SS]` log prefix -- see test_explain_parity.py."""
    return _TIMESTAMP_RE.sub("[TS]", output)


def _build_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_pyproject: str = ""
) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    extra_pyproject = extra_pyproject.replace(
        "\n[tool.semantic_release.bsr]\n",
        "\n[tool.semantic_release.bsr]\nschema_version = 1\n",
        1,
    )
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


def _invoke_mixed() -> object:
    return CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )


def test_summary_false_matches_no_bsr_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nsummary = false\n",
    )
    explicit_false = _invoke_mixed()

    pyproject = tmp_path / "proj" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").split("[tool.semantic_release.bsr]")[0],
        encoding="utf-8",
    )
    no_bsr_table = _invoke_mixed()

    assert explicit_false.exit_code == no_bsr_table.exit_code == 0
    assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)
    assert "release plan" not in explicit_false.output


def test_summary_off_stdout_and_exit_code_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Separated-stream proof (not merged like the test above): with `bsr`
    entirely absent, `--print` stdout is ONLY the version, and is
    byte-identical whether `summary = false` is explicit or the table is
    absent altogether.
    """
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nsummary = false\n",
    )
    explicit_false = CliRunner(mix_stderr=False).invoke(
        main, ["--noop", "version", "--print"]
    )

    pyproject = proj / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").split("[tool.semantic_release.bsr]")[0],
        encoding="utf-8",
    )
    no_bsr_table = CliRunner(mix_stderr=False).invoke(
        main, ["--noop", "version", "--print"]
    )

    assert no_bsr_table.exit_code == explicit_false.exit_code == 0
    assert no_bsr_table.stdout == explicit_false.stdout
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?\n", no_bsr_table.stdout)


def test_summary_on_never_writes_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with `summary = true`, stdout carries ONLY the version -- never the table."""
    _build_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nsummary = true\n",
    )
    result = CliRunner(mix_stderr=False).invoke(main, ["--noop", "version", "--print"])
    assert result.exit_code == 0
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?\n", result.stdout)
    assert "release plan" in result.stderr
