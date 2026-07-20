"""
Drop-in parity proof for `bsr.explain` (C1).

`explain = false` explicit must be byte-identical to no `[tool.
semantic_release.bsr]` table at all -- same stdout, same stderr, same exit
code -- for both a real release and a benign no-op.
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
    """
    Strip RichHandler's `[HH:MM:SS]` log timestamp prefix before comparing
    two separate CLI invocations -- otherwise a real (unrelated) clock tick
    between the two runs makes an otherwise byte-identical parity comparison
    flaky. Mirrors `test_path_filter_parity.py`'s choice to compare
    meaningful content rather than the truly raw `.output` string.
    """
    return _TIMESTAMP_RE.sub("[TS]", output)


def _build_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_pyproject: str = ""
) -> Path:
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


def _invoke() -> object:
    return CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )


def test_explain_false_matches_no_bsr_table_on_real_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nexplain = false\n",
    )
    explicit_false = _invoke()

    pyproject = tmp_path / "proj" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").split("[tool.semantic_release.bsr]")[0],
        encoding="utf-8",
    )
    no_bsr_table = _invoke()

    assert explicit_false.exit_code == no_bsr_table.exit_code == 0
    assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)


def test_explain_false_matches_no_bsr_table_on_benign_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nexplain = false\n",
    )
    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    computed_version = printed.output.strip().splitlines()[-1]
    Repo(str(proj)).create_tag(f"v{computed_version}")

    explicit_false = _invoke()

    pyproject = proj / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").split("[tool.semantic_release.bsr]")[0],
        encoding="utf-8",
    )
    no_bsr_table = _invoke()

    assert explicit_false.exit_code == no_bsr_table.exit_code == 0
    assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)
    assert "has already been released" in explicit_false.output
