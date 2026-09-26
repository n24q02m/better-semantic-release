"""
End-to-end round-trip test for the plan snapshot contract (W1.4).

``plan --write`` → ``verify --plan`` → ``publish --plan`` must work as one
chain on real git fixture repositories (no network): the artifact produced by
`plan` is consumable by `verify` (pre-release drift gate) and `publish`
(opt-in publish gate), and every consumer fails closed when the repository
drifts from the snapshot.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from git import Actor, Repo

from semantic_release.hvcs import Github

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import RunCliFn

_AUTHOR = Actor("demo", "demo@example.com")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repo released once at `v0.1.0` with one feat commit pending."""
    proj = tmp_path / "proj"
    _write(
        proj / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n",
    )
    _write(proj / "apps" / "api" / "x.py", "print('api')\n")
    repo = Repo.init(proj, initial_branch="main")
    repo.index.add(["pyproject.toml", "apps/api/x.py"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    _write(proj / "apps" / "web" / "y.py", "print('web v2')\n")
    repo.index.add(["apps/web/y.py"])
    repo.index.commit("feat: update web page", author=_AUTHOR, committer=_AUTHOR)
    monkeypatch.chdir(proj)
    return proj


def _run(run_cli: RunCliFn, *args: str):
    return run_cli(list(args), env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"})


def _plan_snapshot(proj: Path) -> dict:
    return json.loads((proj / "release-plan.json").read_text(encoding="utf-8"))


def test_roundtrip_plan_verify_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    repo = Repo(str(proj))

    # 1. Plan: write the JSON snapshot artifact.
    result = _run(run_cli, "plan", "--format", "json", "--write", "release-plan.json")
    assert result.exit_code == 0
    snapshot = _plan_snapshot(proj)
    assert snapshot["version"] == "0.2.0"
    assert snapshot["tag"] == "v0.2.0"
    assert snapshot["head_sha"] == str(repo.head.commit.hexsha)

    # Byte-level round-trip: the artifact is exactly the schema-v1
    # serialization of the parsed model (model_validate loses nothing).
    from semantic_release.bsr.plan import ReleasePlan

    parsed = ReleasePlan.model_validate(snapshot)
    artifact = (proj / "release-plan.json").read_text(encoding="utf-8")
    assert artifact == json.dumps(parsed.to_document(), indent=2) + "\n"

    # 2. Verify: the fresh snapshot matches the repository.
    result = _run(run_cli, "verify", "--offline", "--plan", "release-plan.json")
    assert result.exit_code == 0

    # 3. Publish: simulate the `version` step (tag-only flow: HEAD unchanged),
    #    then publish under the plan gate.
    repo.create_tag("v0.2.0")
    with mock.patch.object(Github, Github.upload_dists.__name__):
        result = _run(
            run_cli, "publish", "--format", "json", "--plan", "release-plan.json"
        )
    assert result.exit_code == 0, str(result.stdout)
    doc = json.loads(str(result.stdout))
    assert doc["published"] is True
    assert doc["tag"] == "v0.2.0"


def test_verify_plan_fails_closed_on_head_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    assert (
        _run(run_cli, "plan", "--format", "json", "--write", "release-plan.json")
    ).exit_code == 0

    # Repository moves after the plan was written (repo reset to the tag:
    # computed version drops to the already-released 0.1.0 -> version drift).
    repo = Repo(str(proj))
    repo.git.reset("--hard", "v0.1.0")
    result = _run(
        run_cli,
        "verify",
        "--offline",
        "--format",
        "json",
        "--plan",
        "release-plan.json",
    )
    assert result.exit_code == 1
    doc = json.loads(str(result.stdout))
    codes = {c["code"] for c in doc["checks"] if c["status"] == "fail"}
    assert "PLAN_HEAD_DRIFT" in codes
    assert "PLAN_VERSION_DRIFT" in codes
    assert doc["blocked"] is True


def test_verify_plan_fails_closed_when_planned_tag_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    assert (
        _run(run_cli, "plan", "--format", "json", "--write", "release-plan.json")
    ).exit_code == 0

    # The release is cut: the snapshot is consumed, verify must refuse.
    Repo(str(proj)).create_tag("v0.2.0")
    result = _run(
        run_cli,
        "verify",
        "--offline",
        "--format",
        "json",
        "--plan",
        "release-plan.json",
    )
    assert result.exit_code == 1
    doc = json.loads(str(result.stdout))
    codes = {c["code"] for c in doc["checks"] if c["status"] == "fail"}
    assert "PLAN_TAG_EXISTS" in codes


@pytest.mark.parametrize(
    "content,match",
    [
        ("{not json", None),
        (
            json.dumps(
                {
                    "schema_version": 2,
                    "released": True,
                    "head_sha": "a" * 40,
                }
            ),
            "unsupported plan schema_version",
        ),
    ],
)
def test_verify_plan_fails_closed_on_invalid_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_cli: RunCliFn,
    content: str,
    match: str | None,
) -> None:
    _build_repo(tmp_path, monkeypatch)
    (tmp_path / "proj" / "bad-plan.json").write_text(content, encoding="utf-8")
    result = _run(
        run_cli,
        "verify",
        "--offline",
        "--format",
        "json",
        "--plan",
        "bad-plan.json",
    )
    assert result.exit_code == 1
    doc = json.loads(str(result.stdout))
    codes = {c["code"] for c in doc["checks"] if c["status"] == "fail"}
    assert "PLAN_SNAPSHOT_INVALID" in codes
    if match is not None:
        invalid = next(c for c in doc["checks"] if c["code"] == "PLAN_SNAPSHOT_INVALID")
        assert match in invalid["why"]


def test_publish_plan_fails_closed_on_tag_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    assert (
        _run(run_cli, "plan", "--format", "json", "--write", "release-plan.json")
    ).exit_code == 0
    Repo(str(proj)).create_tag("v0.2.0")

    with mock.patch.object(Github, Github.upload_dists.__name__):
        result = _run(
            run_cli, "publish", "--tag", "v0.1.0", "--plan", "release-plan.json"
        )
    assert result.exit_code == 1
    assert "PLAN_TAG_MISMATCH" in str(result.stdout + result.stderr)


def test_publish_plan_fails_closed_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    assert (
        _run(run_cli, "plan", "--format", "json", "--write", "release-plan.json")
    ).exit_code == 0

    # Cut the planned release, then move the repository beyond it: the
    # recomputed version no longer matches the snapshot.
    repo = Repo(str(proj))
    repo.create_tag("v0.2.0")
    _write(proj / "apps" / "api" / "x.py", "print('api v3')\n")
    repo.index.add(["apps/api/x.py"])
    repo.index.commit("fix: api", author=_AUTHOR, committer=_AUTHOR)

    with mock.patch.object(Github, Github.upload_dists.__name__):
        result = _run(run_cli, "publish", "--plan", "release-plan.json")
    assert result.exit_code == 1
    assert "PLAN_VERSION_DRIFT" in str(result.stdout + result.stderr)


def test_publish_plan_fails_closed_on_unreadable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_repo(tmp_path, monkeypatch)
    Repo(str(proj)).create_tag("v0.2.0")
    (proj / "bad-plan.json").write_text("{nope", encoding="utf-8")

    with mock.patch.object(Github, Github.upload_dists.__name__):
        result = _run(run_cli, "publish", "--plan", "bad-plan.json")
    assert result.exit_code == 1
    assert "Plan snapshot rejected" in str(result.stdout + result.stderr)
