"""
CLI tests for the branch-sync row of `semantic-release verify` and `doctor`.

The project keeps a GitHub remote URL so HVCS parsing works as in a real
checkout, while `url.<bare>.insteadOf` sends every fetch and push to a local
bare repository. `GIT_ALLOW_PROTOCOL=file` makes git refuse any transport
other than local files, so a wrong mapping fails loudly instead of reaching
the network.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from git import Actor, Repo

from semantic_release.cli.commands.main import main

from tests.conftest import get_cli_runner

_AUTHOR = Actor("demo", "demo@example.com")
_REMOTE_URL = "https://github.com/example-owner/example-repo.git"


@pytest.fixture(autouse=True)
def _local_transport_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_ACTOR", raising=False)


def _commit(repo: Repo, message: str, filename: str) -> str:
    (Path(str(repo.working_dir)) / filename).write_text(
        f"{message}\n", encoding="utf-8"
    )
    repo.index.add([filename])
    return str(repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR).hexsha)


def _build_tracked_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ignore_token_for_push: bool = False,
) -> tuple[Repo, Path]:
    """A released project whose `main` tracks `origin/main`, in sync."""
    bare = tmp_path / "remote.git"
    Repo.init(str(bare), bare=True, initial_branch="main")

    proj = tmp_path / "proj"
    proj.mkdir()
    remote_table = (
        "\n[tool.semantic_release.remote]\nignore_token_for_push = true\n"
        if ignore_token_for_push
        else ""
    )
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n"
        f"{remote_table}"
        '\n[tool.semantic_release.bsr]\nschema_version = 1\nregistry = "none"\n',
        encoding="utf-8",
    )
    repo = Repo.init(proj, initial_branch="main")
    repo.index.add(["pyproject.toml"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")
    repo.create_remote("origin", _REMOTE_URL)
    repo.git.config(f"url.{bare.as_uri()}.insteadOf", _REMOTE_URL)
    repo.git.push("--set-upstream", "origin", "main")
    monkeypatch.chdir(proj)
    return repo, bare


def _advance_remote(bare: Path, scratch: Path, *, tag: str | None = None) -> str:
    other = Repo.clone_from(str(bare), str(scratch))
    sha = _commit(other, "feat: change from another clone", "remote.txt")
    other.git.push("origin", "main")
    if tag is not None:
        other.create_tag(tag)
        other.git.push("origin", tag)
    return sha


def _invoke(*args: str) -> object:
    return get_cli_runner().invoke(main, list(args), env={"GH_TOKEN": "test-token"})


def _row(output: str, code: str) -> dict:
    doc = json.loads(output)
    return next(c for c in doc["checks"] if c["code"] == code)


# ---------------------------------------------------------------------------
# verify


def test_verify_in_sync_branch_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_tracked_repo(tmp_path, monkeypatch)
    result = _invoke("verify")
    out = str(result.stdout)
    assert result.exit_code == 0, out
    assert "BRANCH_SYNC" in out
    assert "RESULT: OK" in out


def test_verify_blocks_a_branch_behind_its_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, bare = _build_tracked_repo(tmp_path, monkeypatch)
    _advance_remote(bare, tmp_path / "other")
    result = _invoke("verify")
    out = str(result.stdout)
    assert result.exit_code == 1, out
    assert "BRANCH_BEHIND" in out
    assert "[fix: git pull --ff-only]" in out
    assert "RESULT: BLOCKED" in out


def test_verify_blocks_a_branch_ahead_of_its_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _build_tracked_repo(tmp_path, monkeypatch)
    _commit(repo, "feat: local only", "local.txt")
    result = _invoke("verify", "--format", "json")
    assert result.exit_code == 1
    row = _row(str(result.stdout), "BRANCH_AHEAD")
    assert row["status"] == "fail"
    assert row["severity"] == "blocker"
    assert row["fix"] == "git push origin HEAD:main"


def test_verify_blocks_a_diverged_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, bare = _build_tracked_repo(tmp_path, monkeypatch)
    _advance_remote(bare, tmp_path / "other")
    _commit(repo, "feat: local only", "local.txt")
    result = _invoke("verify", "--format", "json")
    assert result.exit_code == 1
    doc = json.loads(str(result.stdout))
    assert doc["blocked"] is True
    assert "BRANCH_DIVERGED" in {c["code"] for c in doc["checks"]}


def test_verify_offline_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, bare = _build_tracked_repo(tmp_path, monkeypatch)
    before = repo.git.rev_parse("origin/main")
    _advance_remote(bare, tmp_path / "other")
    result = _invoke("verify", "--offline", "--format", "json")
    assert result.exit_code == 0
    row = _row(str(result.stdout), "BRANCH_SYNC")
    assert row["status"] == "skip"
    assert "may be stale" in row["why"]
    assert repo.git.rev_parse("origin/main") == before


def test_verify_noop_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, bare = _build_tracked_repo(tmp_path, monkeypatch)
    before = repo.git.rev_parse("origin/main")
    _advance_remote(bare, tmp_path / "other")
    result = _invoke("--noop", "verify")
    assert result.exit_code == 0
    assert "may be stale" in str(result.stdout)
    assert repo.git.rev_parse("origin/main") == before


def test_verify_fetch_imports_no_tags_and_leaves_head_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, bare = _build_tracked_repo(tmp_path, monkeypatch)
    head_before = str(repo.head.commit.hexsha)
    _advance_remote(bare, tmp_path / "other", tag="v9.9.9")
    _invoke("verify")
    repo = Repo(str(repo.working_dir))
    assert [str(t) for t in repo.tags] == ["v0.1.0"]
    assert str(repo.head.commit.hexsha) == head_before


def test_verify_unreachable_remote_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = _build_tracked_repo(tmp_path, monkeypatch, ignore_token_for_push=True)
    gone = tmp_path / "gone.git"
    repo.git.config("--remove-section", f"url.{(tmp_path / 'remote.git').as_uri()}")
    repo.git.config(f"url.{gone.as_uri()}.insteadOf", _REMOTE_URL)
    result = _invoke("verify", "--format", "json")
    assert result.exit_code == 1
    row = _row(str(result.stdout), "BRANCH_SYNC_UNCONFIRMED")
    assert row["status"] == "fail"
    assert "--offline" in row["fix"]


@pytest.mark.parametrize("command", ["verify", "doctor"])
@pytest.mark.parametrize(
    "extra_args, ignore_token, expect_offline, expect_token_url",
    [
        ((), False, False, True),
        ((), True, False, False),
        (("--offline",), False, True, None),
        (("--noop",), False, True, None),
    ],
)
def test_cli_wires_offline_and_the_hvcs_fetch_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    extra_args: tuple[str, ...],
    ignore_token: bool,
    expect_offline: bool,
    expect_token_url: bool | None,
) -> None:
    # The fallback behaviour itself is covered in test_branch_sync.py; this
    # pins what each command hands to the check.
    _build_tracked_repo(tmp_path, monkeypatch, ignore_token_for_push=ignore_token)
    module = importlib.import_module(f"semantic_release.cli.commands.{command}")
    real = module.check_branch_sync
    seen: dict[str, object] = {}

    def spy(repo_dir: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return real(repo_dir, **kwargs)

    monkeypatch.setattr(module, "check_branch_sync", spy)
    global_args = [arg for arg in extra_args if arg == "--noop"]
    command_args = [arg for arg in extra_args if arg != "--noop"]
    result = _invoke(*global_args, command, *command_args)
    assert result.exit_code == 0, str(result.stdout)
    assert seen["offline"] is expect_offline
    fetch_url = seen["fetch_url"]
    if expect_token_url is None:
        assert fetch_url is None
    else:
        assert isinstance(fetch_url, str)
        assert ("test-token@" in fetch_url) is expect_token_url


def test_verify_help_mentions_the_upstream_fetch() -> None:
    result = _invoke("verify", "--help")
    assert result.exit_code == 0
    assert "upstream" in str(result.stdout)


# ---------------------------------------------------------------------------
# doctor


def test_doctor_reports_a_behind_branch_as_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, bare = _build_tracked_repo(tmp_path, monkeypatch)
    _advance_remote(bare, tmp_path / "other")
    result = _invoke("doctor")
    out = str(result.stdout)
    assert result.exit_code == 0, out  # report only without --strict
    assert "BRANCH_BEHIND" in out
    assert "RESULT: BLOCKED" in out

    strict = _invoke("doctor", "--strict")
    assert strict.exit_code == 1


def test_doctor_offline_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, bare = _build_tracked_repo(tmp_path, monkeypatch)
    before = repo.git.rev_parse("origin/main")
    _advance_remote(bare, tmp_path / "other")
    result = _invoke("doctor", "--offline", "--strict", "--format", "json")
    assert result.exit_code == 0
    row = _row(str(result.stdout), "BRANCH_SYNC")
    assert row["status"] == "skip"
    assert "may be stale" in row["why"]
    assert repo.git.rev_parse("origin/main") == before


def test_doctor_in_sync_json_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_tracked_repo(tmp_path, monkeypatch)
    result = _invoke("doctor", "--format", "json")
    assert result.exit_code == 0
    row = _row(str(result.stdout), "BRANCH_SYNC")
    assert row["status"] == "pass"
    assert row["severity"] == "blocker"


def test_doctor_rejects_offline_with_check_registry() -> None:
    result = _invoke("doctor", "--offline", "--check-registry")
    assert result.exit_code == 2
    assert "--offline cannot be combined with --check-registry" in str(result.output)


def test_doctor_help_lists_offline() -> None:
    result = _invoke("doctor", "--help")
    assert result.exit_code == 0
    assert "--offline" in str(result.stdout)
