"""
CLI integration for `bsr.actionable_errors` (C2): exercises the real
`# BSR-PATCH` seams in `cli/cli_context.py` (`_init_raw_config` /
`_init_runtime_ctx`) and `cli/commands/version.py` -- not `format_actionable`
in isolation (see test_messages.py for that).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.cli.commands.main import main
from semantic_release.errors import MissingGitRemote, ParserLoadError

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("t", "t@t")
_TIMESTAMP_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}\]")


def _normalized(output: str) -> str:
    """
    Strip RichHandler's `[HH:MM:SS]` log prefix before comparing two
    separate invocations -- see test_explain_parity.py for why.
    """
    return _TIMESTAMP_RE.sub("[TS]", output)


def _build_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extra_pyproject: str = "",
    subdir: str = "proj",
) -> Path:
    proj = tmp_path / subdir
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


def _invoke_print() -> object:
    return CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])


class TestValidationErrorAtInitRawConfig:
    """#931-style: an invalid `branches.<name>.match` regex."""

    _BAD_BRANCH_CONFIG = (
        "\n[tool.semantic_release.branches.main]\nmatch = '(unterminated'\n"
    )

    def test_off_by_default_keeps_raw_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(tmp_path, monkeypatch, extra_pyproject=self._BAD_BRANCH_CONFIG)
        result = _invoke_print()
        assert result.exit_code == 1
        assert "INVALID CONFIGURATION" not in result.output

    def test_on_enriches_message_pointing_at_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject=self._BAD_BRANCH_CONFIG
            + "\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        result = _invoke_print()
        assert result.exit_code == 1
        assert "INVALID CONFIGURATION" in result.output
        assert "match" in result.output

    def test_explicit_off_matches_no_bsr_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject=self._BAD_BRANCH_CONFIG
            + "\n[tool.semantic_release.bsr]\nactionable_errors = false\n",
        )
        explicit_false = _invoke_print()

        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject=self._BAD_BRANCH_CONFIG,
            subdir="proj2",
        )
        no_bsr_table = _invoke_print()

        assert explicit_false.exit_code == no_bsr_table.exit_code == 1
        assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)

    def test_real_bad_commit_parser_also_enriched_here(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        A genuine repro (no monkeypatch): a bogus `commit_parser` string
        surfaces as a pydantic `ValidationError` right here, at
        `_init_raw_config` -- `RawConfig`'s own `model_validator` resolves
        the parser eagerly and pydantic wraps the underlying
        `ParserLoadError` -- so it never reaches `_init_runtime_ctx` bare
        (see `TestParserLoadErrorAtInitRuntimeCtx`'s docstring).
        """
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject='commit_parser = "bogus_no_colon"\n'
            "\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        result = _invoke_print()
        assert result.exit_code == 1
        assert "INVALID CONFIGURATION" in result.output
        assert "module:Class" in result.output


class TestParserLoadErrorAtInitRuntimeCtx:
    """
    `ParserLoadError` (raised at `cli/config.py:682` etc.) is currently
    UNCAUGHT in `_init_runtime_ctx`'s except tuple. Exercised via monkeypatch
    of `RuntimeContext.from_raw_config` -- empirically, a bogus
    `commit_parser` string (e.g. `"bogus_no_colon"`) never reaches this seam
    bare: `RawConfig`'s own `model_validator` resolves the parser eagerly, so
    pydantic wraps the underlying `ParserLoadError` into a `ValidationError`
    at `_init_raw_config` instead (see `TestValidationErrorAtInitRawConfig`,
    already generically handled there). This isolates the
    `_init_runtime_ctx` seam directly, matching `test_version_hook.py`'s
    `run_guards` monkeypatch style; `test_messages.py` covers the mapping.
    """

    _MSG = "Unrecognized commit parser value: 'bogus'."

    def _boom(self, *_args: object, **_kwargs: object) -> None:
        raise ParserLoadError(self._MSG)

    def test_off_by_default_still_crashes_uncaught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        result = _invoke_print()
        assert result.exit_code != 0
        assert isinstance(result.exception, ParserLoadError)
        assert "PARSER LOAD FAILED" not in result.output

    def test_on_enriches_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        result = _invoke_print()
        assert result.exit_code == 1
        assert "PARSER LOAD FAILED" in result.output
        assert "commit_parser" in result.output

    def test_explicit_off_matches_no_bsr_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = false\n",
        )
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        explicit_false = _invoke_print()

        _build_project(tmp_path, monkeypatch, subdir="proj2")
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        no_bsr_table = _invoke_print()

        assert explicit_false.exit_code == no_bsr_table.exit_code
        assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)


class TestMissingGitRemoteAtInitRuntimeCtx:
    """
    #1205/#1293-adjacent "missing remote" error: `MissingGitRemote` (raised
    at `cli/config.py:662`) is currently UNCAUGHT in `_init_runtime_ctx`'s
    except tuple. Exercised via monkeypatch of `RuntimeContext.from_raw_config`
    -- empirically, a *genuinely* absent `origin` remote raises GitPython's
    `GitCommandError` at that call site (out of scope here: catching it would
    mean pattern-matching arbitrary git-command failures, far beyond the
    "missing remote" case), not the `ValueError` `MissingGitRemote` maps
    from. This isolates the seam itself, matching `test_version_hook.py`'s
    `run_guards` monkeypatch style; `test_messages.py` covers the mapping.
    """

    _MSG = "Unable to locate remote named 'origin'."

    def _boom(self, *_args: object, **_kwargs: object) -> None:
        raise MissingGitRemote(self._MSG)

    def test_off_by_default_still_crashes_uncaught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        result = _invoke_print()
        assert result.exit_code != 0
        assert isinstance(result.exception, MissingGitRemote)
        assert "GIT REMOTE NOT FOUND" not in result.output

    def test_on_enriches_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        result = _invoke_print()
        assert result.exit_code == 1
        assert "GIT REMOTE NOT FOUND" in result.output
        assert "git remote add" in result.output

    def test_explicit_off_matches_no_bsr_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = false\n",
        )
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        explicit_false = _invoke_print()

        _build_project(tmp_path, monkeypatch, subdir="proj2")
        monkeypatch.setattr(
            "semantic_release.cli.cli_context.RuntimeContext.from_raw_config",
            self._boom,
        )
        no_bsr_table = _invoke_print()

        assert explicit_false.exit_code == no_bsr_table.exit_code
        assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)


class TestPrereleaseBumpMismatchAtVersionCommand:
    """
    #1442: `next_version()` -> `_increment_version()` raising the bare
    ValueError. Exercised via monkeypatch (same established pattern as
    `test_version_hook.py`'s `run_guards` monkeypatch) since reproducing the
    real upstream trigger (`default_bump_level=prerelease_revision` +
    non-prerelease base) needs a much heavier fixture than the seam itself
    warrants; `test_messages.py` unit-tests the mapping in isolation.
    """

    _MISMATCH_MSG = (
        "Cannot increment a non-prerelease version with a prerelease level bump"
    )

    def _boom(self, **_kwargs: object) -> None:
        raise ValueError(self._MISMATCH_MSG)

    def test_off_by_default_reraises_uncaught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "semantic_release.cli.commands.version.next_version", self._boom
        )
        result = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version"])
        assert result.exit_code != 0
        assert isinstance(result.exception, ValueError)
        assert "PRERELEASE BUMP MISMATCH" not in result.output

    def test_on_reports_actionable_message_and_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        monkeypatch.setattr(
            "semantic_release.cli.commands.version.next_version", self._boom
        )
        result = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version"])
        assert result.exit_code == 1
        assert "PRERELEASE BUMP MISMATCH" in result.output


class TestTagFormatSanityNote:
    """#1196: git tags exist but none matched `tag_format`."""

    def test_off_by_default_prints_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = _build_project(tmp_path, monkeypatch)
        Repo(str(proj)).create_tag("release-1.0.0")  # doesn't match "v{version}"
        result = _invoke_print()
        assert "TAG_FORMAT MISMATCH" not in result.output

    def test_on_reports_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        Repo(str(proj)).create_tag("release-1.0.0")
        result = _invoke_print()
        assert result.exit_code == 0
        assert "TAG_FORMAT MISMATCH" in result.output
        assert "1 git tag(s)" in result.output

    def test_on_no_note_when_tag_matches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = _build_project(
            tmp_path,
            monkeypatch,
            extra_pyproject="\n[tool.semantic_release.bsr]\nactionable_errors = true\n",
        )
        printed = _invoke_print()
        assert printed.exit_code == 0
        computed_version = printed.output.strip().splitlines()[-1]
        Repo(str(proj)).create_tag(f"v{computed_version}")

        result = _invoke_print()
        assert "TAG_FORMAT MISMATCH" not in result.output
