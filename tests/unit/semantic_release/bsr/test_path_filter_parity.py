"""
Drop-in parity proof for the M2 monorepo path-filter (issue #168).

Two independent proofs, both on a monorepo-shaped git fixture (commits
spanning `apps/api` and `apps/web`):

1. `test_version_parity_...` -- the REAL CLI (`version --print`), which goes
   through the actual `# BSR-PATCH` wiring in `cli/commands/version.py`
   (`load_bsr_config` -> `make_path_filter` -> `next_version`), computes the
   IDENTICAL version whether `[tool.semantic_release.bsr] path_filter = false`
   is explicit or the whole `bsr` table is absent.
2. `test_changelog_parity_...` -- the same real config-loading chain
   (`load_bsr_config` -> `make_path_filter`) feeding
   `ReleaseHistory.from_git_history`, then rendered through the actual
   changelog template (`render_default_changelog_file`), produces IDENTICAL
   changelog text for the two configs.

Together these show the three `# BSR-PATCH` seams are inert end-to-end
(config parsing through to rendered output) when `path_filter` is off --
not just that a bare `None` argument no-ops the seam in isolation (that is
already covered by `test_path_filter_algorithm.py` /
`test_path_filter_changelog.py` / `test_path_filter_cli.py`).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.bsr.config import BsrConfig, load_bsr_config
from semantic_release.bsr.path_filter import make_path_filter
from semantic_release.changelog.context import ChangelogMode, make_changelog_context
from semantic_release.changelog.release_history import ReleaseHistory
from semantic_release.cli.changelog_writer import render_default_changelog_file
from semantic_release.cli.commands.main import main
from semantic_release.cli.config import ChangelogOutputFormat
from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.hvcs import Github
from semantic_release.version.translator import VersionTranslator
from semantic_release.version.version import Version

if TYPE_CHECKING:
    import pytest

_AUTHOR = Actor("t", "t@t")
_REMOTE_URL = "https://github.com/example-owner/example-repo.git"


def _commit(repo: Repo, relpath: str, content: str, message: str) -> None:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _build_monorepo(tmp_path: Path) -> Repo:
    """
    A minimal 2-component monorepo: one commit touching each of
    `apps/api` and `apps/web`, plus an `origin` remote (PSR resolves the
    hvcs client from it before `--print` returns).
    """
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: add api endpoint")
    _commit(repo, "apps/web/y.py", "y", "feat: add web page")
    repo.create_remote("origin", _REMOTE_URL)
    return repo


def _pyproject_toml(extra_bsr: str) -> str:
    extra_bsr = extra_bsr.replace(
        "\n[tool.semantic_release.bsr]\n",
        "\n[tool.semantic_release.bsr]\nschema_version = 1\n",
        1,
    )
    return (
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        "allow_zero_version = true\n" + extra_bsr
    )


def _print_version() -> str:
    result = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert result.exit_code == 0, result.output
    return next(
        line
        for line in result.output.strip().splitlines()
        if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", line)
    )


def test_version_parity_path_filter_false_matches_no_bsr_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The version the real CLI computes with `path_filter = false` explicit
    is byte-identical to a run with no `[tool.semantic_release.bsr]` table.
    """
    repo = _build_monorepo(tmp_path)
    monkeypatch.chdir(str(repo.working_tree_dir))
    pyproject = Path(str(repo.working_tree_dir)) / "pyproject.toml"

    pyproject.write_text(
        _pyproject_toml(
            "\n[tool.semantic_release.bsr]\n"
            "path_filter = false\n"
            'paths = ["apps/api"]\n'
        ),
        encoding="utf-8",
    )
    version_explicit_false = _print_version()

    pyproject.write_text(_pyproject_toml(""), encoding="utf-8")
    version_no_bsr_table = _print_version()

    assert version_explicit_false == version_no_bsr_table
    # non-trivial: a real bump happened, this isn't two empty/failed runs
    # coincidentally producing the same string.
    assert re.fullmatch(r"\d+\.\d+\.\d+", version_explicit_false)


def test_changelog_parity_path_filter_false_matches_no_bsr_config(
    tmp_path: Path,
) -> None:
    """
    The rendered changelog text produced via the real
    `load_bsr_config` -> `make_path_filter` -> `from_git_history` chain is
    byte-identical whether `path_filter = false` is explicit or the whole
    `bsr` table is absent from config.
    """
    repo = _build_monorepo(tmp_path)
    proj = Path(str(repo.working_tree_dir))

    explicit_false = proj / "pyproject-explicit-false.toml"
    explicit_false.write_text(
        _pyproject_toml(
            "\n[tool.semantic_release.bsr]\n"
            "path_filter = false\n"
            'paths = ["apps/api"]\n'
        ),
        encoding="utf-8",
    )
    no_bsr_table = proj / "pyproject-no-bsr-table.toml"
    no_bsr_table.write_text(_pyproject_toml(""), encoding="utf-8")

    filter_explicit_false = make_path_filter(load_bsr_config(explicit_false), proj)
    filter_no_bsr_table = make_path_filter(load_bsr_config(no_bsr_table), proj)
    assert filter_explicit_false is None
    assert filter_no_bsr_table is None

    def _render(commit_path_filter: object) -> str:
        release_history = ReleaseHistory.from_git_history(
            repo=repo,
            translator=VersionTranslator(),
            commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
            commit_path_filter=commit_path_filter,  # type: ignore[arg-type]
        ).release(
            Version.parse("0.1.0"),
            tagger=_AUTHOR,
            committer=_AUTHOR,
            tagged_date=datetime.now(timezone.utc),
        )
        return render_default_changelog_file(
            output_format=ChangelogOutputFormat.MARKDOWN,
            changelog_context=make_changelog_context(
                hvcs_client=Github(_REMOTE_URL),
                release_history=release_history,
                mode=ChangelogMode.INIT,
                prev_changelog_file=Path("CHANGELOG.md"),
                insertion_flag="",
                # NOTE: False, not True -- with only one release in this fixture's
                # history, that release IS the initial one; masking it would hide
                # the commit descriptions this test asserts on.
                mask_initial_release=False,
            ),
            changelog_style="conventional",
        )

    changelog_explicit_false = _render(filter_explicit_false)
    changelog_no_bsr_table = _render(filter_no_bsr_table)

    assert changelog_explicit_false == changelog_no_bsr_table
    # non-trivial: both commits (across both paths) are present -- proves
    # the filter is truly inert, not just "empty output on both sides".
    lowered = changelog_explicit_false.lower()
    assert "api endpoint" in lowered
    assert "web page" in lowered


def test_make_path_filter_none_regardless_of_config_source(tmp_path: Path) -> None:
    """
    Sanity anchor: an explicit `path_filter=false` BsrConfig and the
    all-defaults BsrConfig (what you get when the table is absent) both
    resolve to `None` through `make_path_filter` -- the fact the two parity
    tests above rely on.
    """
    Repo.init(tmp_path)
    explicit_false = BsrConfig(path_filter=False, paths=("apps/api",))
    absent = BsrConfig()
    assert make_path_filter(explicit_false, tmp_path) is None
    assert make_path_filter(absent, tmp_path) is None
