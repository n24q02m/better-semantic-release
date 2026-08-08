"""
CLI integration for `bsr.jsonout` on `semantic-release publish`.

The second consumer of the contract Task 6 established for `version`: same flag,
same `schema_version`, same absolute guarantee that stdout carries exactly one
JSON document. Each test therefore asserts by calling `json.loads(result.stdout)`
-- if anything else reached stdout, the parse fails, and that IS the assertion.
See test_jsonout_cli.py for the `version` side.

`publish` has four exits and three of them are failures, which is what most of
this file is about: an agent needs the document most on the paths where the
command did not do what was asked.
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

_PYPROJECT = (
    '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
    "[tool.semantic_release]\n"
    'tag_format = "v{version}"\n'
    "allow_zero_version = true\n"
)


def _build_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dists: tuple[str, ...] = (),
    tag: str | None = "v0.1.0",
) -> Path:
    """
    A repo with one commit, optionally tagged, optionally holding built dists.

    `tag=None` is the "no tags found" case: `--tag latest` has nothing to resolve
    against, which is the command's first exit.
    """
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    for name in dists:
        dist_file = proj / "dist" / name
        dist_file.parent.mkdir(parents=True, exist_ok=True)
        dist_file.write_text("x", encoding="utf-8")

    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    if tag is not None:
        repo.create_tag(tag)
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")

    monkeypatch.chdir(proj)
    return proj


def _invoke(*args: str) -> object:
    return CliRunner(mix_stderr=False).invoke(main, ["--noop", "publish", *args])


def test_publish_json_emits_one_document_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # must parse -- nothing else on stdout
    assert payload["schema_version"] == 1
    assert payload["published"] is True
    assert payload["tag"] == "v0.1.0"
    assert payload["assets"] == []


def test_publish_json_lists_the_distributions_it_handled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The names are re-derived from the configured globs, because `upload_dists`
    reports only a count. Sorted and POSIX-separated so the document does not
    vary by filesystem order or by platform.
    """
    _build_repo(tmp_path, monkeypatch, dists=("demo-0.1.0.whl", "demo-0.1.0.tar.gz"))
    payload = json.loads(_invoke("--format", "json").stdout)

    assert payload["assets"] == ["dist/demo-0.1.0.tar.gz", "dist/demo-0.1.0.whl"]


def test_publish_json_honours_an_explicit_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    payload = json.loads(_invoke("--tag", "v0.1.0", "--format", "json").stdout)

    assert payload["published"] is True
    assert payload["tag"] == "v0.1.0"


def test_publish_json_reports_an_unknown_tag_as_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The command exits 1 on a tag that is not in the repository. The document
    still has to be there and still has to parse, or a caller's `json.loads`
    blows up exactly when it most needs to read the result.
    """
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--tag", "v9.9.9", "--format", "json")

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["published"] is False
    assert payload["tag"] == "v9.9.9"


def test_publish_json_reports_no_tags_found_with_a_null_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `--tag latest` against an untagged repo resolves to nothing, so the field is
    null rather than the literal string "latest" -- the document reports what was
    operated on, not what was asked for.
    """
    _build_repo(tmp_path, monkeypatch, tag=None)
    result = _invoke("--format", "json")

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["published"] is False
    assert payload["tag"] is None
    assert payload["assets"] == []


def test_publish_default_format_writes_nothing_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stock `publish` narrates only to stderr; --format must not change that."""
    _build_repo(tmp_path, monkeypatch)
    result = _invoke()

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "would have uploaded files" in result.stderr


def test_publish_narration_stays_on_stderr_under_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    result = _invoke("--format", "json")

    assert "would have uploaded files" not in result.stdout
    assert "would have uploaded files" in result.stderr


def test_publish_verbose_logging_does_not_reach_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-vv` is the loudest this CLI gets; the document must still be alone."""
    _build_repo(tmp_path, monkeypatch)
    result = CliRunner(mix_stderr=False).invoke(
        main, ["-vv", "--noop", "publish", "--format", "json"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["schema_version"] == 1
    assert "DEBUG" in result.stderr


def test_publish_invalid_format_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_repo(tmp_path, monkeypatch)
    assert _invoke("--format", "yaml").exit_code != 0
