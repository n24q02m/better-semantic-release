"""
CLI integration for `bsr.jsonout` (AX): exercises the real `# BSR-PATCH` seam in
`cli/commands/version.py` -- not `bsr/jsonout.py` in isolation (see
test_jsonout.py for that).

The contract under test is narrow and absolute: under `--format json`, stdout
carries exactly one JSON document and nothing else, for every way the command
can end. Each test therefore asserts by calling `json.loads(result.stdout)` --
if anything else reached stdout, the parse fails, and that IS the assertion.
"""

from __future__ import annotations

import json
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
        # Same reasoning as test_summary_cli.py: without this, PSR forces a MAJOR
        # bump out of 0.x.x regardless of which commits matched.
        "allow_zero_version = true\n" + extra_bsr
    )


def _build_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    extra_bsr: str = "",
    new_commit: str | None = "feat: update web page",
) -> Path:
    """
    A repo released once at `v0.1.0`, optionally with one commit on top.

    The three no-release-reason cases are reached by varying `new_commit`:
    a releasable type releases, a non-releasable one (`chore:`) gives
    NO_QUALIFYING_COMMITS, and `None` leaves HEAD on the released tag, which is
    ALREADY_RELEASED_NOOP.
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

    if new_commit is not None:
        _write(proj / "apps" / "web" / "y.py", "print('web v2')\n")
        repo.index.add(["apps/web/y.py"])
        repo.index.commit(new_commit, author=_AUTHOR, committer=_AUTHOR)

    monkeypatch.chdir(proj)
    return proj


def _invoke(*args: str) -> object:
    return CliRunner(mix_stderr=False).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push", *args]
    )


_COMPONENTS_TOML = (
    "\n[[tool.semantic_release.bsr.components]]\n"
    'name = "api"\n'
    'paths = ["apps/api"]\n'
    "[[tool.semantic_release.bsr.components]]\n"
    'name = "web"\n'
    'paths = ["apps/web"]\n'
)


def test_json_format_emits_one_document_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # must parse -- nothing else on stdout
    assert payload["schema_version"] == 1
    assert payload["released"] is True
    assert payload["version"] == "0.2.0"
    assert payload["tag"] == "v0.2.0"
    assert payload["previous_version"] == "0.1.0"
    assert payload["reason"] is None
    assert payload["is_prerelease"] is False


def test_json_format_reports_a_benign_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HEAD sits on the released tag: nothing new at all since the release."""
    _build_repo(tmp_path, monkeypatch, new_commit=None)
    result = _invoke("--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["released"] is False
    assert payload["reason"] == "ALREADY_RELEASED_NOOP"
    assert payload["commit_count"] == 0
    assert payload["version"] == "0.1.0"


def test_json_format_reports_non_qualifying_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A `chore:` commit is scanned but does not qualify for a bump.

    This is the case the reason field exists for: stock PSR reports it with the
    same "already been released" line as the benign no-op above.
    """
    _build_repo(tmp_path, monkeypatch, new_commit="chore: tidy up")
    result = _invoke("--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["released"] is False
    assert payload["reason"] == "NO_QUALIFYING_COMMITS"
    assert payload["commit_count"] == 1
    assert payload["type_counts"] == {}


def test_json_format_carries_bump_stats_without_the_prose_explainer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bsr.explain` is off by default; the document reports the data anyway."""
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")

    payload = json.loads(result.stdout)
    assert payload["level_bump"] == "minor"
    assert payload["commit_count"] == 1
    # The parser reports its own category names, not the raw commit types.
    assert payload["type_counts"] == {"features": 1}
    assert "why this bump" not in result.stderr.lower()


def test_json_format_includes_components_when_summary_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nsummary = true\n" + _COMPONENTS_TOML,
    )
    result = _invoke("--format", "json")

    payload = json.loads(result.stdout)
    by_name = {c["name"]: c for c in payload["components"]}
    assert by_name["web"]["would_release"] is True
    assert by_name["web"]["level"] == "MINOR"
    assert by_name["api"]["would_release"] is False
    # The human table still renders -- to stderr, where it does not collide.
    assert "release plan" in result.stderr


def test_json_format_has_empty_components_without_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")

    assert json.loads(result.stdout)["components"] == []


def test_print_under_json_format_still_yields_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `--print` exits before releasing, so `released` is false -- but the caller
    asked for JSON, so it gets the document rather than a bare version line.
    """
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--print", "--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["released"] is False
    assert payload["version"] == "0.2.0"
    assert payload["reason"] is None


def test_print_last_released_under_json_format_yields_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--print-last-released", "--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["previous_version"] == "0.1.0"
    assert payload["version"] is None


def test_default_format_output_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No --format: stdout is the bare version line, exactly as before."""
    _build_repo(tmp_path, monkeypatch)
    result = _invoke()

    assert result.exit_code == 0
    assert result.stdout == "0.2.0\n"


def test_default_format_print_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    assert _invoke("--print").stdout == "0.2.0\n"


def test_default_format_print_last_released_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    assert _invoke("--print-last-released").stdout == "0.1.0\n"


def test_narration_stays_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The human banner is stderr-only, so it cannot collide with the document."""
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")

    assert "The next version is" not in result.stdout
    assert "The next version is" in result.stderr


def test_verbose_logging_does_not_reach_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `-vv` is the loudest this CLI gets: it attaches a rich log handler and emits
    debug records throughout. The document still has to be the only thing on
    stdout, so this is the case most likely to break the contract in the field.
    """
    _build_repo(tmp_path, monkeypatch)
    result = CliRunner(mix_stderr=False).invoke(
        main,
        [
            "-vv",
            "--noop",
            "version",
            "--no-commit",
            "--no-tag",
            "--no-push",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["schema_version"] == 1
    assert "DEBUG" in result.stderr


def test_json_mode_does_not_affect_a_later_default_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `--format json` must leave no process-wide residue.

    `main()` gets invoked in-process by tests and by anything embedding it, so a
    JSON run followed by an ordinary one has to produce the ordinary output --
    that is the "default output is unchanged" guarantee, applied twice over.
    """
    _build_repo(tmp_path, monkeypatch)
    _invoke("--format", "json")
    after = _invoke()

    assert after.stdout == "0.2.0\n"
    assert "The next version is" in after.stderr


def test_invalid_format_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "yaml")

    assert result.exit_code != 0
