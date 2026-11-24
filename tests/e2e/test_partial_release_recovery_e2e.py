"""Deterministic failure-injection E2E tests for partial-release recovery (BSR-6)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from git import Actor, Repo

from semantic_release.bsr.publishers import (
    OUTCOME_ALREADY_EXISTS,
    ShellPublisher,
)
from semantic_release.bsr.registry import ProbeResult
from semantic_release.bsr.release_publisher import (
    AmbiguousProviderError,
    ProviderError,
    ProviderTransportError,
    publish_manifest,
)
from semantic_release.bsr.tag_race import TagRaceGuardError, check_tag_race
from semantic_release.hvcs import Github

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from tests.conftest import RunCliFn

pytestmark = pytest.mark.e2e

_AUTHOR = Actor("demo", "demo@example.com")
_REPO_SLUG = "n24q02m/better-semantic-release"


class _FakeProvider:
    """In-memory provider supporting fault injection for partial releases."""

    def __init__(self, target_sha: str, *, tag: str = "v1.0.0") -> None:
        self.tag_refs: dict[str, dict[str, Any]] = {
            tag: {"object": {"type": "commit", "sha": target_sha}}
        }
        self.tag_objects: dict[str, dict[str, Any]] = {}
        self.releases: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, list[dict[str, Any]]] = {}
        self.create_errors: list[BaseException] = []
        self.upload_errors: dict[str, BaseException] = {}
        self.latest: dict[str, Any] | None = None

    def get_tag_ref(self, _repository: str, tag: str) -> dict[str, Any]:
        return self.tag_refs[tag]

    def get_tag_object(self, _repository: str, sha: str) -> dict[str, Any]:
        return self.tag_objects[sha]

    def get_release_by_tag(self, _repository: str, tag: str) -> dict[str, Any] | None:
        return self.releases.get(tag)

    def create_release(
        self, _repository: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self.create_errors:
            raise self.create_errors.pop(0)
        tag = payload["tag_name"]
        release = {
            "id": 77,
            "html_url": f"https://github.com/{_REPO_SLUG}/releases/tag/{tag}",
            "tag_name": tag,
            "name": payload["name"],
            "body": payload["body"],
            "draft": payload["draft"],
            "prerelease": payload["prerelease"],
            "make_latest": payload["make_latest"],
        }
        self.releases[tag] = release
        self.assets.setdefault(tag, [])
        return release

    def list_release_assets(
        self, _repository: str, release_id: int, *, page: int, per_page: int
    ) -> list[dict[str, Any]]:
        del per_page
        if page > 1:
            return []
        tag = next(tag for tag, rel in self.releases.items() if rel["id"] == release_id)
        return list(self.assets[tag])

    def upload_asset(
        self,
        _repository: str,
        release_id: int,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        err = self.upload_errors.pop(name, None)
        if err is not None:
            raise err
        tag = next(tag for tag, rel in self.releases.items() if rel["id"] == release_id)
        item = {
            "name": name,
            "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "content_type": content_type,
        }
        self.assets[tag].append(item)
        return item

    def get_latest_release(self, _repository: str) -> dict[str, Any] | None:
        return self.latest


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "proj"
    _write(
        proj / "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\nallow_zero_version = true\n"
        '[tool.semantic_release.bsr]\nschema_version = 1\nregistry = "pypi"\n',
    )
    _write(proj / "app.py", "print('v1')\n")
    repo = Repo.init(proj, initial_branch="main")
    repo.index.add(["pyproject.toml", "app.py"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("v0.1.0")
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    _write(proj / "app.py", "print('v2')\n")
    repo.index.add(["app.py"])
    repo.index.commit("feat: next", author=_AUTHOR, committer=_AUTHOR)
    monkeypatch.chdir(proj)
    return proj


def _write_manifest(
    workspace: Path,
    assets: list[tuple[str, bytes]],
    *,
    tag: str = "v1.0.0",
    target_sha: str | None = None,
) -> Path:
    body = b"release notes\n"
    (workspace / "notes.md").write_bytes(body)
    asset_entries: list[dict[str, Any]] = []
    for name, raw in sorted(assets):
        file_path = workspace / "dist" / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(raw)
        asset_entries.append(
            {
                "name": name,
                "path": f"dist/{name}",
                "size": len(raw),
                "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "content_type": "application/octet-stream",
            }
        )
    manifest = {
        "schema_version": "bsr-release-manifest/v1",
        "repository": _REPO_SLUG,
        "transaction_id": "tx-w10-bsr6-001",
        "tag": tag,
        "target_sha": target_sha or ("a" * 40),
        "release": {
            "name": tag,
            "body_path": "notes.md",
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "draft": False,
            "prerelease": False,
            "make_latest": False,
        },
        "assets": asset_entries,
    }
    manifest_path = workspace / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path


def test_partial_local_tag_then_verify_repair_check_and_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    proj = _build_git_repo(tmp_path, monkeypatch)
    Repo(str(proj)).create_tag("v0.2.0")

    with mock.patch(
        "semantic_release.cli.commands.verify._LIVE_PROBE",
        return_value=ProbeResult.FREE,
    ):
        res = run_cli(
            ["verify", "--repair-check", "--format", "json"],
            env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"},
        )
    assert res.exit_code == 0
    doc = json.loads(str(res.stdout))
    action = next(c for c in doc["checks"] if c["code"] == "REPAIR_NEXT_ACTION")
    assert action["status"] == "fail"
    assert action["severity"] == "warning"

    with mock.patch.object(Github, Github.upload_dists.__name__) as upload:
        pub = run_cli(
            ["publish", "--tag", "v0.2.0"],
            env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"},
        )
    assert pub.exit_code == 0
    upload.assert_called_once()


def test_mid_upload_failure_reuses_uploaded_asset_on_retry(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        [("a.whl", b"wheel-bytes"), ("b.tar.gz", b"tarball-bytes")],
    )
    provider = _FakeProvider("a" * 40)
    provider.upload_errors["b.tar.gz"] = ProviderTransportError("simulated drop")

    with pytest.raises(AmbiguousProviderError, match="upload readback"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=_REPO_SLUG
        )

    resumed = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=_REPO_SLUG
    )
    assert resumed["reused_count"] == 1
    assert resumed["uploaded_count"] == 1


def test_registry_already_exists_short_circuits_publisher(tmp_path: Path) -> None:
    invoked: list[tuple[str, ...]] = []
    pub = ShellPublisher(
        "pypi",
        tmp_path,
        runner=lambda argv: invoked.append(tuple(argv)) or 0,
    )
    outcome = pub.publish("1.0.0", probe=ProbeResult.EXISTS, noop=False)
    assert outcome.state == OUTCOME_ALREADY_EXISTS
    assert invoked == []


def test_remote_asset_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, [("a.whl", b"expected-bytes")])
    provider = _FakeProvider("a" * 40)
    provider.releases["v1.0.0"] = {
        "id": 77,
        "html_url": f"https://github.com/{_REPO_SLUG}/releases/tag/v1.0.0",
        "tag_name": "v1.0.0",
        "name": "v1.0.0",
        "body": "release notes\n",
        "draft": False,
        "prerelease": False,
        "make_latest": False,
    }
    provider.assets["v1.0.0"] = [
        {
            "name": "a.whl",
            "size": len(b"expected-bytes"),
            "digest": "sha256:" + ("0" * 64),
            "content_type": "application/octet-stream",
        }
    ]

    with pytest.raises(ProviderError, match="asset digest, size, or content_type"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=_REPO_SLUG
        )


def test_tag_race_detects_remote_tag_and_head_move(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    Repo.init(bare, bare=True)
    work = tmp_path / "work"
    _write(work / "f.txt", "1\n")
    repo = Repo.init(work, initial_branch="main")
    repo.index.add(["f.txt"])
    c1 = repo.index.commit("feat: c1", author=_AUTHOR, committer=_AUTHOR)
    repo.create_remote("origin", str(bare))
    repo.remotes.origin.push("main:main")

    _write(work / "f.txt", "2\n")
    repo.index.add(["f.txt"])
    c2 = repo.index.commit("feat: c2", author=_AUTHOR, committer=_AUTHOR)
    repo.remotes.origin.push("main:main")

    with pytest.raises(TagRaceGuardError) as exc_head:
        check_tag_race(
            work,
            remote_name="origin",
            branch="main",
            planned_tag="v0.2.0",
            planned_head_sha=c1.hexsha,
        )
    assert exc_head.value.code == "CONCURRENT_RUNNER"

    repo.create_tag("v0.2.0")
    repo.remotes.origin.push("v0.2.0")
    with pytest.raises(TagRaceGuardError) as exc_tag:
        check_tag_race(
            work,
            remote_name="origin",
            branch="main",
            planned_tag="v0.2.0",
            planned_head_sha=c2.hexsha,
        )
    assert exc_tag.value.code == "TAG_RACE"


def test_monorepo_manifest_publish_recovers_after_transient_create_error(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(
        tmp_path,
        [("pkg-a-1.0.0.whl", b"pkg-a"), ("pkg-b-1.0.0.whl", b"pkg-b")],
        tag="pkg-monorepo-v1.0.0",
    )
    provider = _FakeProvider("a" * 40, tag="pkg-monorepo-v1.0.0")
    provider.create_errors.append(ProviderTransportError("502 gateway"))

    with pytest.raises(AmbiguousProviderError, match="create release readback"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=_REPO_SLUG
        )

    resumed = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=_REPO_SLUG
    )
    assert resumed["created"] is True
    assert resumed["uploaded_count"] == 2
    assert resumed["reused_count"] == 0


def test_registry_exists_without_local_tag_blocks_verify_repair_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCliFn
) -> None:
    _build_git_repo(tmp_path, monkeypatch)
    with mock.patch(
        "semantic_release.cli.commands.verify._LIVE_PROBE",
        return_value=ProbeResult.EXISTS,
    ):
        res = run_cli(
            ["verify", "--repair-check", "--format", "json"],
            env={Github.DEFAULT_ENV_TOKEN_NAME: "test-token"},
        )
    assert res.exit_code == 1
    doc = json.loads(str(res.stdout))
    action = next(c for c in doc["checks"] if c["code"] == "REPAIR_NEXT_ACTION")
    assert action["severity"] == "blocker"
    assert action["status"] == "fail"
