from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import stat
import sys
from contextlib import closing, contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

import pytest

from semantic_release.bsr import release_publisher as publisher
from semantic_release.bsr.release_publisher import (
    AmbiguousProviderError,
    HttpGithubProvider,
    ManifestError,
    ProviderError,
    ProviderHttpError,
    ProviderTransportError,
    PublishLimits,
    peel_tag,
    prepare_manifest,
    publish_manifest,
    reconcile,
)

REPOSITORY = "n24q02m/better-semantic-release"
TARGET_SHA = "a" * 40
BODY = b"release notes\n"


class FakeProvider:
    def __init__(self) -> None:
        self.tag_refs: dict[str, dict[str, Any]] = {
            "v1.0.0": {"object": {"type": "commit", "sha": TARGET_SHA}}
        }
        self.tag_objects: dict[str, dict[str, Any]] = {}
        self.releases: dict[str, dict[str, Any]] = {}
        self.assets: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, Any]] = []
        self.create_error: BaseException | None = None
        self.upload_errors: dict[str, BaseException] = {}
        self.latest: dict[str, Any] | None = None
        self.page_size = 100

    def get_tag_ref(self, _repository: str, tag: str) -> dict[str, Any]:
        self.calls.append(("get_tag_ref", tag))
        return self.tag_refs[tag]

    def get_tag_object(self, _repository: str, sha: str) -> dict[str, Any]:
        self.calls.append(("get_tag_object", sha))
        return self.tag_objects[sha]

    def get_release_by_tag(self, _repository: str, tag: str) -> dict[str, Any] | None:
        self.calls.append(("get_release_by_tag", tag))
        return self.releases.get(tag)

    def create_release(
        self, _repository: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("create_release", payload))
        if self.create_error is not None:
            error = self.create_error
            self.create_error = None
            raise error
        release = _release(payload, release_id=41)
        self.releases[payload["tag_name"]] = release
        self.assets[payload["tag_name"]] = []
        return release

    def list_release_assets(
        self, _repository: str, release_id: int, *, page: int, per_page: int
    ) -> list[dict[str, Any]]:
        del per_page
        self.calls.append(("list_release_assets", page))
        all_assets = self.assets[
            next(
                tag
                for tag, release in self.releases.items()
                if release["id"] == release_id
            )
        ]
        start = (page - 1) * self.page_size
        return all_assets[start : start + self.page_size]

    def upload_asset(
        self,
        _repository: str,
        release_id: int,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        self.calls.append(("upload_asset", name))
        error = self.upload_errors.pop(name, None)
        if error is not None:
            raise error
        tag = next(
            tag for tag, release in self.releases.items() if release["id"] == release_id
        )
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        item = {
            "name": name,
            "size": len(data),
            "digest": digest,
            "content_type": content_type,
        }
        self.assets[tag].append(item)
        return item

    def get_latest_release(self, _repository: str) -> dict[str, Any] | None:
        self.calls.append(("get_latest_release", None))
        return self.latest


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _release(payload: dict[str, Any], *, release_id: int) -> dict[str, Any]:
    return {
        "id": release_id,
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{payload['tag_name']}",
        "tag_name": payload["tag_name"],
        "name": payload["name"],
        "body": payload["body"],
        "draft": payload["draft"],
        "prerelease": payload["prerelease"],
        "make_latest": payload["make_latest"],
    }


def _write_manifest(
    tmp_path: Path,
    *,
    assets: list[tuple[str, bytes]] | None = None,
    tag: str = "v1.0.0",
    target_sha: str = TARGET_SHA,
    make_latest: bool | str = False,
    draft: bool = False,
    prerelease: bool = False,
    body: bytes = BODY,
) -> Path:
    if assets is None:
        assets = [("dist.tar.gz", b"package bytes")]
    body_path = tmp_path / "notes.md"
    body_path.write_bytes(body)
    entries: list[dict[str, Any]] = []
    for name, content in assets:
        path = tmp_path / "dist" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        entries.append(
            {
                "name": name,
                "path": str(path.relative_to(tmp_path)).replace(os.sep, "/"),
                "size": len(content),
                "sha256": "sha256:" + _sha(content),
                "content_type": "application/octet-stream",
            }
        )
    entries.sort(key=lambda item: item["name"])
    manifest = {
        "schema_version": "bsr-release-manifest/v1",
        "repository": REPOSITORY,
        "transaction_id": "tx-20260829-001",
        "tag": tag,
        "target_sha": target_sha,
        "release": {
            "name": "v1.0.0",
            "body_path": "notes.md",
            "body_sha256": _sha(body),
            "draft": draft,
            "prerelease": prerelease,
            "make_latest": make_latest,
        },
        "assets": entries,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path


def _payload_for_manifest(manifest_path: Path) -> dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _seed_release(provider: FakeProvider, manifest_path: Path) -> None:
    payload = _payload_for_manifest(manifest_path)
    body = (manifest_path.parent / payload["release"]["body_path"]).read_text()
    provider.releases[payload["tag"]] = _release(
        {
            "tag_name": payload["tag"],
            "name": payload["release"]["name"],
            "body": body,
            "draft": payload["release"]["draft"],
            "prerelease": payload["release"]["prerelease"],
            "make_latest": payload["release"]["make_latest"],
        },
        release_id=41,
    )
    provider.assets[payload["tag"]] = []


def test_duplicate_manifest_keys_are_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"schema_version":"bsr-release-manifest/v1",'
        '"repository":"n24q02m/better-semantic-release",'
        '"repository":"other/repository"}',
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="duplicate"):
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)


def test_manifest_key_order_is_canonical(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    payload = _payload_for_manifest(manifest)
    reordered = {"repository": payload["repository"], **payload}
    manifest.write_text(json.dumps(reordered, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(ManifestError, match="order"):
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)


def test_path_escape_is_rejected_before_any_provider_call(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    payload = _payload_for_manifest(manifest)
    payload["release"]["body_path"] = "../notes.md"
    manifest.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    provider = FakeProvider()

    with pytest.raises(ManifestError, match="path"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )
    assert provider.calls == []


def test_symlink_input_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_bytes(BODY)
    link = tmp_path / "notes.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    manifest = _write_manifest(tmp_path)

    with pytest.raises(ManifestError, match="symlink"):
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)


def test_size_or_digest_mismatch_is_rejected_while_staging(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    payload = _payload_for_manifest(manifest)
    payload["assets"][0]["size"] += 1
    manifest.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(ManifestError, match="size"):
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)


def test_absent_release_is_created_and_missing_asset_uploaded(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()

    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["created"] is True
    assert result["uploaded_count"] == 1
    assert result["reused_count"] == 0
    assert [call[0] for call in provider.calls] == [
        "get_tag_ref",
        "get_release_by_tag",
        "create_release",
        "get_latest_release",
        "list_release_assets",
        "upload_asset",
        "get_tag_ref",
        "get_release_by_tag",
        "list_release_assets",
        "get_latest_release",
    ]


@pytest.mark.parametrize(
    "requested,wire_value",
    ((False, "false"), (True, "true")),
)
def test_create_release_wires_make_latest_as_string(
    tmp_path: Path, requested: bool, wire_value: str
) -> None:
    manifest = _write_manifest(tmp_path, make_latest=requested)
    provider = FakeProvider()
    if requested:
        provider.latest = {"tag_name": "v1.0.0"}

    publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    payload = next(call[1] for call in provider.calls if call[0] == "create_release")
    assert payload["make_latest"] == wire_value


def test_existing_partial_release_resumes_exactly_and_complete_rerun_reuses_assets(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path, assets=[("a.bin", b"a"), ("b.bin", b"b")])
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.assets["v1.0.0"] = [
        {
            "name": "a.bin",
            "size": 1,
            "digest": "sha256:" + _sha(b"a"),
            "content_type": "application/octet-stream",
        }
    ]

    first = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )
    second = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert first["created"] is False
    assert first["uploaded_count"] == 1
    assert first["reused_count"] == 1
    assert second["uploaded_count"] == 0
    assert second["reused_count"] == 2
    assert [call for call in provider.calls if call[0] == "upload_asset"] == [
        ("upload_asset", "b.bin")
    ]


def test_existing_release_metadata_mismatch_has_no_write_path(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.releases["v1.0.0"]["name"] = "wrong"

    with pytest.raises(ProviderError, match="metadata"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )
    assert all(
        call[0] not in {"create_release", "upload_asset"} for call in provider.calls
    )


def test_foreign_duplicate_and_mismatched_assets_are_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    for existing in (
        [{"name": "foreign.bin", "size": 1, "digest": "sha256:" + _sha(b"x")}],
        [
            {
                "name": "dist.tar.gz",
                "size": 12,
                "digest": "sha256:" + _sha(b"package bytes"),
                "content_type": "application/octet-stream",
            },
            {
                "name": "dist.tar.gz",
                "size": 12,
                "digest": "sha256:" + _sha(b"package bytes"),
                "content_type": "application/octet-stream",
            },
        ],
        [
            {
                "name": "dist.tar.gz",
                "size": 99,
                "digest": "sha256:" + _sha(b"package bytes"),
                "content_type": "application/octet-stream",
            }
        ],
        [{"name": "dist.tar.gz", "size": 12}],
    ):
        provider = FakeProvider()
        _seed_release(provider, manifest)
        provider.assets["v1.0.0"] = existing
        with pytest.raises(ProviderError):
            publish_manifest(
                manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
            )


@pytest.mark.parametrize(
    "content_type",
    (
        pytest.param(None, id="missing"),
        pytest.param("", id="invalid"),
        pytest.param("text/plain", id="mismatched"),
    ),
)
def test_existing_asset_content_type_must_be_exact(
    tmp_path: Path, content_type: str | None
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    asset: dict[str, Any] = {
        "name": "dist.tar.gz",
        "size": 12,
        "digest": "sha256:" + _sha(b"package bytes"),
    }
    if content_type is not None:
        asset["content_type"] = content_type
    provider.assets["v1.0.0"] = [asset]

    with pytest.raises(ProviderError, match="asset"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )


def test_incompatible_latest_state_blocks_asset_upload(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.latest = {"tag_name": "v1.0.0"}

    with pytest.raises(ProviderError, match="latest"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )
    assert all(call[0] != "upload_asset" for call in provider.calls)


def test_ambiguous_create_is_resolved_by_one_readback(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()

    class TimeoutAfterCreate(TimeoutError):
        pass

    original_create = provider.create_release

    def create_then_timeout(repository: str, payload: dict[str, Any]) -> dict[str, Any]:
        original_create(repository, payload)
        raise TimeoutAfterCreate()

    provider.create_release = create_then_timeout  # type: ignore[method-assign]
    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["created"] is True
    assert [call[0] for call in provider.calls].count("create_release") == 1
    assert [call[0] for call in provider.calls].count("get_release_by_tag") == 3


class _TrackedResponse:
    status = 302

    def __init__(self) -> None:
        self.closed = False

    def read(self, _size: int = -1) -> bytes:
        return b"redirect"

    def close(self) -> None:
        self.closed = True


class _TrackedErrorBody:
    def __init__(self) -> None:
        self.closed = False

    def read(self, _size: int = -1) -> bytes:
        return b'{"error":"server"}'

    def close(self) -> None:
        self.closed = True


class _FailureOpener:
    def __init__(
        self,
        failure: str,
        error: BaseException,
        response: _TrackedResponse,
    ) -> None:
        self.failure = failure
        self.error = error
        self.response = response
        self.redirect_handler: Any | None = None

    def build(self, *handlers: Any) -> _FailureOpener:
        self.redirect_handler = next(
            handler
            for handler in handlers
            if isinstance(handler, publisher._SafeRedirectHandler)
        )
        return self

    def open(self, _request: Any, *, timeout: float) -> Any:
        del timeout
        if self.failure == "http-error":
            raise self.error
        if self.failure == "unexpected-status":
            return self.response
        assert self.redirect_handler is not None
        self.redirect_handler.redirect_request(
            _request,
            self.response,
            302,
            "Found",
            {},
            "https://attacker.example/releases",
        )
        raise AssertionError("redirect handler did not reject the target")


@pytest.mark.parametrize(
    "failure", ("http-error", "unexpected-status", "rejected-redirect")
)
def test_mutating_http_failure_is_ambiguous_and_read_back_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest_payload = _payload_for_manifest(manifest_path)
    body = (tmp_path / manifest_payload["release"]["body_path"]).read_text()
    release_payload = {
        "tag_name": manifest_payload["tag"],
        "name": manifest_payload["release"]["name"],
        "body": body,
        "draft": manifest_payload["release"]["draft"],
        "prerelease": manifest_payload["release"]["prerelease"],
        "make_latest": manifest_payload["release"]["make_latest"],
    }
    provider = FakeProvider()
    provider.releases[manifest_payload["tag"]] = _release(
        release_payload, release_id=41
    )
    provider.assets[manifest_payload["tag"]] = []
    http_provider = HttpGithubProvider("ghs_secret_value")
    response = _TrackedResponse()
    error_body = _TrackedErrorBody()
    error = publisher.urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases",
        500,
        "server error",
        None,
        error_body,
    )
    opener = _FailureOpener(failure, error, response)

    monkeypatch.setattr(
        publisher, "_github_ssl_context", publisher.ssl.create_default_context
    )
    monkeypatch.setattr(publisher.urllib.request, "build_opener", opener.build)

    def create_release(repository: str, value: dict[str, Any]) -> dict[str, Any]:
        provider.calls.append(("create_release", value))
        return http_provider.create_release(repository, value)

    provider.create_release = create_release  # type: ignore[method-assign]
    with closing(prepare_manifest(manifest_path, tmp_path)) as prepared:
        result, created = publisher._create_release(
            provider, REPOSITORY, prepared.manifest, body
        )

    assert created is True
    assert result["id"] == 41
    assert [call[0] for call in provider.calls].count("create_release") == 1
    assert [call[0] for call in provider.calls].count("get_release_by_tag") == 1
    assert (error_body if failure == "http-error" else response).closed is True


def test_release_readback_does_not_require_unreturned_make_latest_field(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.releases["v1.0.0"].pop("make_latest")

    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["created"] is False


def test_ambiguous_upload_same_bytes_converges_without_replay(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    original_upload = provider.upload_asset

    def upload_then_timeout(
        repository: str, release_id: int, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        original_upload(repository, release_id, name, content_type, data)
        raise TimeoutError()

    provider.upload_asset = upload_then_timeout  # type: ignore[method-assign]
    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["uploaded_count"] == 1
    assert [call for call in provider.calls if call[0] == "upload_asset"] == [
        ("upload_asset", "dist.tar.gz")
    ]


def test_upload_response_content_type_mismatch_requires_readback(
    tmp_path: Path,
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    original_upload = provider.upload_asset

    def upload_with_mismatched_response(
        repository: str, release_id: int, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        response = dict(
            original_upload(repository, release_id, name, content_type, data)
        )
        response["content_type"] = "text/plain"
        return response

    provider.upload_asset = upload_with_mismatched_response  # type: ignore[method-assign]
    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["uploaded_count"] == 1
    assert [call[0] for call in provider.calls].count("list_release_assets") == 3


def test_ambiguous_upload_different_bytes_fails(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)

    def conflicting_upload(
        repository: str, release_id: int, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        provider.calls.append(("upload_asset", name))
        provider.assets["v1.0.0"] = [
            {
                "name": name,
                "size": 1,
                "digest": "sha256:" + _sha(b"x"),
                "content_type": "application/octet-stream",
            }
        ]
        raise TimeoutError()

    provider.upload_asset = conflicting_upload  # type: ignore[method-assign]

    with pytest.raises(ProviderError, match="digest|size"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )
    assert [call for call in provider.calls if call[0] == "upload_asset"] == [
        ("upload_asset", "dist.tar.gz")
    ]


def test_annotated_tags_are_peeled_recursively(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    provider.tag_refs["v1.0.0"] = {"object": {"type": "tag", "sha": "b" * 40}}
    provider.tag_objects["b" * 40] = {"object": {"type": "tag", "sha": "c" * 40}}
    provider.tag_objects["c" * 40] = {"object": {"type": "commit", "sha": TARGET_SHA}}

    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["release_id"] == 41
    assert [call for call in provider.calls if call[0] == "get_tag_object"] == [
        ("get_tag_object", "b" * 40),
        ("get_tag_object", "c" * 40),
        ("get_tag_object", "b" * 40),
        ("get_tag_object", "c" * 40),
    ]


def test_annotated_tag_cycle_and_wrong_target_are_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    for tag_objects, _ in (
        (
            {"b" * 40: {"object": {"type": "tag", "sha": "b" * 40}}},
            TARGET_SHA,
        ),
        (
            {"b" * 40: {"object": {"type": "commit", "sha": "c" * 40}}},
            TARGET_SHA,
        ),
    ):
        provider = FakeProvider()
        provider.tag_refs["v1.0.0"] = {"object": {"type": "tag", "sha": "b" * 40}}
        provider.tag_objects = tag_objects
        with pytest.raises(ProviderError, match="tag"):
            publish_manifest(
                manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
            )


@pytest.mark.skipif(
    not getattr(publisher, "_DESCRIPTOR_STAGING", False),
    reason="descriptor-anchored staging is unavailable on this platform",
)
def test_descriptor_staging_rejects_ancestor_swap_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    original_directory = tmp_path / "dist"
    outside_directory = tmp_path / "outside"
    moved_directory = tmp_path / "dist-original"
    outside_directory.mkdir()
    (outside_directory / "dist.tar.gz").write_bytes(b"outside bytes")

    swapped = False

    def swap_before_open(workspace: Path, parts: tuple[str, ...], scope: str) -> None:
        nonlocal swapped
        if scope == "asset" and parts == ("dist",):
            original_directory.rename(moved_directory)
            original_directory.symlink_to(outside_directory, target_is_directory=True)
            swapped = True

    monkeypatch.setattr(publisher, "_before_workspace_component_open", swap_before_open)
    with pytest.raises(ManifestError, match="symlink|file"):
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)
    assert swapped is True


def test_asset_pagination_rejects_duplicate_page_entries(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, assets=[("a.bin", b"a"), ("b.bin", b"b")])
    provider = FakeProvider()
    provider.page_size = 1
    _seed_release(provider, manifest)
    provider.assets["v1.0.0"] = [
        {
            "name": "a.bin",
            "size": 1,
            "digest": "sha256:" + _sha(b"a"),
            "content_type": "application/octet-stream",
        },
        {
            "name": "a.bin",
            "size": 1,
            "digest": "sha256:" + _sha(b"a"),
            "content_type": "application/octet-stream",
        },
    ]

    with pytest.raises(ProviderError, match="duplicate"):
        publish_manifest(
            manifest,
            workspace=tmp_path,
            provider=provider,
            repository=REPOSITORY,
            limits=PublishLimits(asset_page_size=1),
        )


def test_computed_legacy_latest_policy_is_rejected(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, make_latest="legacy")

    with pytest.raises(ManifestError, match="make_latest"):
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)


@pytest.mark.parametrize("flag", ("draft", "prerelease"))
def test_latest_release_flags_reject_before_provider_construction(
    tmp_path: Path, flag: str
) -> None:
    manifest = _write_manifest(tmp_path, make_latest=True, **{flag: True})
    constructed = False

    def provider_factory(token: str) -> FakeProvider:
        nonlocal constructed
        constructed = True
        return FakeProvider()

    with pytest.raises(ManifestError, match="make_latest"):
        publish_manifest(
            manifest,
            workspace=tmp_path,
            provider_factory=provider_factory,
            token="ghs_secret_value",
            repository=REPOSITORY,
        )
    assert constructed is False


def test_latest_policy_rejects_candidate_that_is_latest(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, make_latest=False)
    provider = FakeProvider()
    provider.latest = {"tag_name": "v1.0.0"}

    with pytest.raises(ProviderError, match="latest"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )
    assert all(call[0] != "upload_asset" for call in provider.calls)


def test_latest_policy_requires_stable_release_to_be_latest(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, make_latest=True)
    provider = FakeProvider()
    provider.latest = {"tag_name": "v0.9.0"}

    with pytest.raises(ProviderError, match="latest"):
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )


def test_output_preflight_fails_before_provider_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    output = tmp_path / "github-output"
    output.mkdir()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    constructed = False

    def provider_factory(token: str) -> FakeProvider:
        nonlocal constructed
        constructed = True
        return FakeProvider()

    with pytest.raises(ProviderError, match="output"):
        publish_manifest(
            manifest,
            workspace=tmp_path,
            provider_factory=provider_factory,
            token="ghs_secret_value",
            repository=REPOSITORY,
        )
    assert constructed is False


@pytest.mark.skipif(
    os.name == "nt", reason="Windows locks open output files against replacement"
)
def test_output_channel_retains_inode_across_path_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    replaced = tmp_path / "github-output-replaced"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    channel = publisher._open_output_channel()
    assert channel is not None
    provider = FakeProvider()

    def provider_factory(token: str) -> FakeProvider:
        output.rename(replaced)
        output.write_text("", encoding="utf-8")
        return provider

    try:
        result = publish_manifest(
            manifest,
            workspace=tmp_path,
            provider_factory=provider_factory,
            token="ghs_secret_value",
            repository=REPOSITORY,
            output_channel=channel,
        )
        publisher._write_outputs(result, channel)
    finally:
        channel.close()

    assert "transaction_id=" + result["transaction_id"] in replaced.read_text()
    assert output.read_text() == ""


def test_invalid_manifest_does_not_construct_provider_or_expose_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "manifest.json"
    secret = "ghs_super_secret_token"
    manifest.write_text(
        '{"schema_version":"wrong","repository":"' + secret + '"}', encoding="utf-8"
    )
    constructed = False

    def provider_factory(token: str) -> FakeProvider:
        nonlocal constructed
        constructed = True
        return FakeProvider()

    with pytest.raises(ManifestError):
        publish_manifest(
            manifest,
            workspace=tmp_path,
            provider_factory=provider_factory,
            token=secret,
            repository=REPOSITORY,
        )
    assert constructed is False
    assert secret not in capsys.readouterr().err


@pytest.mark.parametrize("wire_value", ("false", "true"))
def test_http_create_release_wires_make_latest_enum(
    monkeypatch: pytest.MonkeyPatch, wire_value: str
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status = 201

        def __init__(self) -> None:
            self.done = False
            self.closed = False

        def read(self, _size: int = -1) -> bytes:
            if self.done:
                return b""
            self.done = True
            return b"{}"

        def close(self) -> None:
            self.closed = True

    response = Response()

    class Opener:
        def open(self, request: Any, *, timeout: float) -> Response:
            del timeout
            captured["request"] = request
            return response

    monkeypatch.setattr(
        publisher, "_github_ssl_context", publisher.ssl.create_default_context
    )
    monkeypatch.setattr(
        publisher.urllib.request, "build_opener", lambda *_handlers: Opener()
    )
    provider = HttpGithubProvider("ghs_secret_value")

    provider.create_release(
        REPOSITORY, {"tag_name": "v1.0.0", "make_latest": wire_value}
    )

    assert captured["request"].data == (
        b'{"tag_name":"v1.0.0","make_latest":"' + wire_value.encode() + b'"}'
    )
    assert response.closed is True


def test_github_provider_uses_pinned_api_version_and_bounded_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status = 200

        def __init__(self) -> None:
            self.done = False
            self.closed = False

        def read(self, _size: int = -1) -> bytes:
            if self.done:
                return b""
            self.done = True
            return b'{"tag_name":"v1.0.0"}'

        def close(self) -> None:
            self.closed = True

    response = Response()
    captured: dict[str, Any] = {"response": response}

    class Opener:
        def open(self, request: Any, *, timeout: float) -> Response:
            captured["request"] = request
            captured["timeout"] = timeout
            return response

    tls_context = publisher.ssl.create_default_context()
    tls_context.minimum_version = publisher.ssl.TLSVersion.TLSv1_2
    monkeypatch.setattr(publisher, "_github_ssl_context", lambda: tls_context)

    def build_opener(*handlers: Any) -> Opener:
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(publisher.urllib.request, "build_opener", build_opener)
    provider = HttpGithubProvider(
        "ghs_secret_value", limits=PublishLimits(timeout_seconds=3)
    )

    assert provider.get_latest_release(REPOSITORY) == {"tag_name": "v1.0.0"}
    request = captured["request"]
    assert request.full_url == (
        "https://api.github.com/repos/n24q02m/better-semantic-release/releases/latest"
    )
    assert request.headers["X-github-api-version"] == "2022-11-28"
    assert captured["timeout"] == 3
    handlers = captured["handlers"]
    proxy_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, publisher.urllib.request.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    https_handlers = [
        handler
        for handler in handlers
        if isinstance(handler, publisher.urllib.request.HTTPSHandler)
    ]
    assert len(https_handlers) == 1
    assert https_handlers[0]._context is tls_context
    assert response.closed is True
    assert (
        https_handlers[0]._context.minimum_version >= publisher.ssl.TLSVersion.TLSv1_2
    )
    assert https_handlers[0]._context.verify_mode == publisher.ssl.CERT_REQUIRED
    assert https_handlers[0]._context.check_hostname is True


def test_github_provider_upload_targets_upload_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Response:
        status = 201

        def __init__(self) -> None:
            self.done = False

        def read(self, _size: int = -1) -> bytes:
            if self.done:
                return b""
            self.done = True
            return b"{}"

        def close(self) -> None:
            pass

    class Opener:
        def open(self, request: Any, *, timeout: float) -> Response:
            del timeout
            captured["request"] = request
            return Response()

    monkeypatch.setattr(
        publisher, "_github_ssl_context", publisher.ssl.create_default_context
    )
    monkeypatch.setattr(
        publisher.urllib.request, "build_opener", lambda *_handlers: Opener()
    )
    provider = HttpGithubProvider("ghs_secret_value")

    provider.upload_asset(
        REPOSITORY,
        41,
        "dist.tar.gz",
        "application/octet-stream",
        b"asset",
    )

    assert captured["request"].full_url == (
        "https://uploads.github.com/repos/"
        "n24q02m/better-semantic-release/releases/41/assets?name=dist.tar.gz"
    )


def test_github_provider_rejects_oversized_response_without_body_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_body = b'{"secret":"' + b"x" * 128 + b'"}'

    class Response:
        status = 200

        def __init__(self) -> None:
            self.done = False

        def read(self, _size: int = -1) -> bytes:
            if self.done:
                return b""
            self.done = True
            return secret_body

    class Opener:
        def open(self, _request: Any, *, timeout: float) -> Response:
            del timeout
            return Response()

    monkeypatch.setattr(
        publisher.urllib.request, "build_opener", lambda *_handlers: Opener()
    )
    provider = HttpGithubProvider(
        "ghs_secret_value", limits=PublishLimits(max_response_bytes=16)
    )

    with pytest.raises(ProviderError) as error:
        provider.get_latest_release(REPOSITORY)
    assert secret_body.decode() not in str(error.value)


@pytest.mark.parametrize(
    "operation,response_body,response_limit,response_status",
    (
        ("create", b"not-json", 64, 201),
        ("upload", b"not-json", 64, 201),
        ("create", b"x" * 32, 16, 201),
        ("upload", b"x" * 32, 16, 201),
        ("create", b"{}", 64, 202),
        ("upload", b"{}", 64, 202),
    ),
)
def test_successful_write_response_failure_is_ambiguous_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    response_body: bytes,
    response_limit: int,
    response_status: int,
) -> None:
    class Response:
        status = response_status

        def __init__(self) -> None:
            self.done = False
            self.closed = False

        def read(self, _size: int = -1) -> bytes:
            if self.done:
                return b""
            self.done = True
            return response_body

        def close(self) -> None:
            self.closed = True

    response = Response()

    class Opener:
        def open(self, _request: Any, *, timeout: float) -> Response:
            del timeout
            return response

    monkeypatch.setattr(
        publisher.urllib.request, "build_opener", lambda *_handlers: Opener()
    )
    provider = HttpGithubProvider(
        "ghs_secret_value", limits=PublishLimits(max_response_bytes=response_limit)
    )

    with pytest.raises(AmbiguousProviderError):
        if operation == "create":
            provider.create_release(REPOSITORY, {})
        else:
            provider.upload_asset(
                REPOSITORY,
                41,
                "dist.tar.gz",
                "application/octet-stream",
                b"asset",
            )
    assert response.closed is True


def test_github_provider_closes_http_error_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Body:
        def __init__(self) -> None:
            self.closed = False

        def read(self, _size: int = -1) -> bytes:
            return b'{"error":"denied"}'

        def close(self) -> None:
            self.closed = True

    body = Body()
    error = publisher.urllib.error.HTTPError(
        "https://api.github.com/repos/x/y/releases",
        500,
        "server error",
        None,
        body,
    )

    class Opener:
        def open(self, _request: Any, *, timeout: float) -> Any:
            del timeout
            raise error

    monkeypatch.setattr(
        publisher.urllib.request, "build_opener", lambda *_handlers: Opener()
    )
    provider = HttpGithubProvider("ghs_secret_value")

    with pytest.raises(ProviderError):
        provider.get_latest_release(REPOSITORY)
    assert body.closed is True


def test_redirect_handler_allows_only_github_upload_host() -> None:
    handler = publisher._SafeRedirectHandler(max_redirects=1)
    request = publisher.urllib.request.Request("https://api.github.com/repos/x/y")
    fp = _TrackedResponse()

    with pytest.raises(ProviderError, match="url"):
        handler.redirect_request(
            request,
            fp,
            302,
            "Found",
            {},
            "https://attacker.example/upload",
        )
    assert fp.closed is True


# Validation, staging, reconciliation, output and CLI boundaries.

_GITHUB_ENV = (
    "GITHUB_OUTPUT",
    "GITHUB_REPOSITORY",
    "GITHUB_WORKSPACE",
    "INPUT_MANIFEST",
    "INPUT_TOKEN",
)
_API = f"https://api.github.com/repos/{REPOSITORY}"
_UPLOADS = f"https://uploads.github.com/repos/{REPOSITORY}"


@pytest.fixture(params=("descriptor", "portable"))
def staging_mode(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> str:
    """
    Run a staging test through both open strategies.

    ``portable`` is the strategy used where ``dir_fd``/``O_NOFOLLOW`` are absent
    (Windows natively); forcing it elsewhere emulates such a platform.
    """
    mode = str(request.param)
    if mode == "descriptor":
        if not publisher._DESCRIPTOR_STAGING:
            pytest.skip("descriptor-anchored staging is unavailable on this platform")
    else:
        monkeypatch.setattr(publisher, "_DESCRIPTOR_STAGING", False)
    return mode


@pytest.fixture
def github_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Start from an environment without the variables a CI runner injects."""
    for name in _GITHUB_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _set_manifest_field(
    manifest: Path, keys: tuple[str | int, ...], value: Any
) -> None:
    payload = _payload_for_manifest(manifest)
    target: Any = payload
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    manifest.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _prepare_error(manifest: Path | str, workspace: Path, **kwargs: Any) -> str:
    with pytest.raises(ManifestError) as error:
        prepare_manifest(
            manifest, workspace, expected_repository=REPOSITORY, **kwargs
        ).close()
    assert str(error.value) == f"publisher {error.value.code}"
    return error.value.code


def _publish_error(
    manifest: Path, provider: FakeProvider, **kwargs: Any
) -> ProviderError:
    with pytest.raises(ProviderError) as error:
        publish_manifest(
            manifest,
            workspace=manifest.parent,
            provider=provider,
            repository=REPOSITORY,
            **kwargs,
        )
    return error.value


def _call_names(provider: FakeProvider) -> list[str]:
    return [call[0] for call in provider.calls]


def _stat(
    mode: int, *, uid: int = 0, ino: int = 1, dev: int = 1, size: int = 0
) -> os.stat_result:
    return os.stat_result((mode, ino, dev, 1, uid, 0, size, 0, 0, 0))


@contextmanager
def _unreadable(path: Path) -> Iterator[None]:
    """Make an existing file impossible to open for reading."""
    if os.name == "nt":
        winapi = pytest.importorskip("_winapi")
        handle = winapi.CreateFile(
            str(path),
            winapi.GENERIC_READ,
            0,
            winapi.NULL,
            winapi.OPEN_EXISTING,
            0,
            winapi.NULL,
        )
        try:
            yield
        finally:
            winapi.CloseHandle(handle)
        return
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permission bits")
    path.chmod(0)
    try:
        yield
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.parametrize(
    "field",
    (
        "max_manifest_bytes",
        "max_file_bytes",
        "max_response_bytes",
        "max_pages",
        "asset_page_size",
        "max_tag_depth",
        "max_redirects",
    ),
)
def test_publish_limits_reject_non_positive_bounds(field: str) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        PublishLimits(**{field: 0})


def test_publish_limits_reject_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        PublishLimits(timeout_seconds=0)


def test_relative_manifest_path_stages_exact_bytes(
    tmp_path: Path, staging_mode: str
) -> None:
    manifest = _write_manifest(tmp_path)

    with closing(
        prepare_manifest("manifest.json", tmp_path, expected_repository=REPOSITORY)
    ) as prepared:
        assert prepared.manifest.assets[0].digest == "sha256:" + _sha(b"package bytes")
        assert prepared.manifest.raw_sha256 == _sha(manifest.read_bytes())
        assert prepared.body.read_bytes() == BODY
        assert prepared.assets["dist.tar.gz"].read_bytes() == b"package bytes"
        staging_directory = prepared.body.path.parent
        assert staging_directory != tmp_path
    assert not staging_directory.exists()


@pytest.mark.parametrize(
    "raw,code",
    (
        pytest.param(b"\xff\xfe{}", "manifest encoding", id="encoding"),
        pytest.param(b"{not json", "manifest json", id="syntax"),
        pytest.param(b'{"a":NaN}', "manifest json", id="nan-constant"),
        pytest.param(b"[]", "manifest object", id="array"),
        pytest.param(b'{"a":"x\\u0001"}', "manifest control character", id="control"),
    ),
)
def test_unparseable_manifest_is_rejected_with_value_safe_code(
    tmp_path: Path, raw: bytes, code: str
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(raw)

    assert _prepare_error(manifest, tmp_path) == code


@pytest.mark.parametrize(
    "keys,value,code",
    (
        pytest.param(
            ("schema_version",),
            "bsr-release-manifest/v2",
            "manifest schema",
            id="schema",
        ),
        pytest.param(("repository",), "", "manifest repository", id="repo-empty"),
        pytest.param(
            ("repository",), "not a repo", "manifest repository", id="repo-shape"
        ),
        pytest.param(
            ("repository",),
            "other/repository",
            "manifest repository mismatch",
            id="repo-mismatch",
        ),
        pytest.param(
            ("transaction_id",), 7, "manifest transaction_id", id="transaction-type"
        ),
        pytest.param(
            ("transaction_id",),
            "-x",
            "manifest transaction_id",
            id="transaction-shape",
        ),
        pytest.param(("tag",), "/v1.0.0", "manifest tag", id="tag-leading-slash"),
        pytest.param(("tag",), "v1.0.0~1", "manifest tag", id="tag-revision-char"),
        pytest.param(("tag",), "release/.hidden", "manifest tag", id="tag-dot-part"),
        pytest.param(("target_sha",), "z" * 40, "manifest target_sha", id="sha-hex"),
        pytest.param(("target_sha",), "A" * 40, "manifest target_sha", id="sha-case"),
        pytest.param(("release",), [], "manifest release object", id="release-type"),
        pytest.param(
            ("release", "body_sha256"),
            "Z" * 64,
            "manifest body_sha256",
            id="body-digest",
        ),
        pytest.param(
            ("release", "draft"), "false", "manifest release flags", id="draft-type"
        ),
        pytest.param(("assets",), {}, "manifest assets array", id="assets-type"),
        pytest.param(("assets", 0), "dist", "manifest asset object", id="asset-type"),
        pytest.param(("assets", 0, "name"), "..", "manifest asset name", id="name"),
        pytest.param(("assets", 0, "size"), True, "manifest asset size", id="size"),
        pytest.param(
            ("assets", 0, "sha256"),
            "sha512:" + "0" * 64,
            "manifest asset sha256",
            id="asset-digest",
        ),
        pytest.param(
            ("assets", 0, "path"), "notes.md", "manifest duplicate path", id="path"
        ),
        pytest.param(
            ("assets", 0, "name"),
            "\ud800",
            "manifest canonical json",
            id="lone-surrogate",
        ),
    ),
)
def test_invalid_manifest_field_is_rejected(
    tmp_path: Path, keys: tuple[str | int, ...], value: Any, code: str
) -> None:
    manifest = _write_manifest(tmp_path)
    _set_manifest_field(manifest, keys, value)

    assert _prepare_error(manifest, tmp_path) == code


@pytest.mark.parametrize(
    "names",
    (
        pytest.param(("b.bin", "a.bin"), id="unsorted"),
        pytest.param(("a.bin", "a.bin"), id="duplicate"),
    ),
)
def test_asset_names_must_be_sorted_and_unique(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    manifest = _write_manifest(tmp_path, assets=[("a.bin", b"a"), ("b.bin", b"b")])
    _set_manifest_field(manifest, ("assets", 0, "name"), names[0])
    _set_manifest_field(manifest, ("assets", 1, "name"), names[1])

    assert _prepare_error(manifest, tmp_path) == "manifest asset order"


def test_missing_body_file_is_rejected(tmp_path: Path, staging_mode: str) -> None:
    manifest = _write_manifest(tmp_path)
    (tmp_path / "notes.md").unlink()

    assert _prepare_error(manifest, tmp_path) == "manifest body file"


@pytest.mark.parametrize("scope", ("manifest", "body"))
def test_directory_in_place_of_input_file_is_rejected(
    tmp_path: Path, staging_mode: str, scope: str
) -> None:
    manifest = _write_manifest(tmp_path)
    target = manifest if scope == "manifest" else tmp_path / "notes.md"
    target.unlink()
    target.mkdir()
    # Descriptor staging opens the directory and fails when wrapping it as a
    # binary stream; the portable strategy rejects it from lstat metadata.
    outcome = "read" if staging_mode == "descriptor" else "file"

    assert _prepare_error(manifest, tmp_path) == f"manifest {scope} {outcome}"


@pytest.mark.parametrize("location", ("workspace-itself", "outside-workspace"))
def test_manifest_must_be_a_file_inside_the_workspace(
    tmp_path: Path, staging_mode: str, location: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = _write_manifest(tmp_path)
    manifest = workspace if location == "workspace-itself" else outside

    assert _prepare_error(manifest, workspace) == "manifest manifest path"


@pytest.mark.parametrize("kind", ("missing", "regular-file"))
def test_unusable_workspace_is_rejected(
    tmp_path: Path, staging_mode: str, kind: str
) -> None:
    workspace = tmp_path / "workspace"
    if kind == "regular-file":
        workspace.write_bytes(b"not a directory")
    expected = (
        "manifest manifest file"
        if staging_mode == "descriptor"
        else "manifest workspace"
    )

    assert _prepare_error("manifest.json", workspace) == expected


def test_symlinked_asset_directory_is_rejected(
    tmp_path: Path, staging_mode: str
) -> None:
    manifest = _write_manifest(tmp_path)
    real_directory = tmp_path / "dist-real"
    (tmp_path / "dist").rename(real_directory)
    try:
        (tmp_path / "dist").symlink_to(real_directory, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    # O_DIRECTORY|O_NOFOLLOW on a symlinked directory fails with ENOTDIR rather
    # than ELOOP, so descriptor staging reports the generic file code.
    outcome = "file" if staging_mode == "descriptor" else "symlink"

    assert _prepare_error(manifest, tmp_path) == f"manifest asset {outcome}"


@pytest.mark.parametrize("scope", ("manifest", "body"))
def test_unopenable_input_is_rejected(
    tmp_path: Path, staging_mode: str, scope: str
) -> None:
    manifest = _write_manifest(tmp_path)
    target = manifest if scope == "manifest" else tmp_path / "notes.md"

    with _unreadable(target):
        code = _prepare_error(manifest, tmp_path)

    assert code == f"manifest {scope} file"


@pytest.mark.parametrize(
    "limits,code",
    (
        pytest.param(
            PublishLimits(max_manifest_bytes=16),
            "manifest manifest size",
            id="manifest",
        ),
        pytest.param(PublishLimits(max_file_bytes=4), "manifest body size", id="body"),
    ),
)
def test_oversized_input_is_rejected(
    tmp_path: Path, staging_mode: str, limits: PublishLimits, code: str
) -> None:
    manifest = _write_manifest(tmp_path)

    assert _prepare_error(manifest, tmp_path, limits=limits) == code


def test_asset_digest_mismatch_is_rejected(tmp_path: Path, staging_mode: str) -> None:
    manifest = _write_manifest(tmp_path)
    _set_manifest_field(
        manifest, ("assets", 0, "sha256"), "sha256:" + _sha(b"other bytes!")
    )

    assert _prepare_error(manifest, tmp_path) == "manifest asset digest"


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_junction_in_body_path_is_rejected(tmp_path: Path) -> None:
    winapi = pytest.importorskip("_winapi")
    manifest = _write_manifest(tmp_path)
    real_directory = tmp_path / "docs-real"
    real_directory.mkdir()
    (real_directory / "notes.md").write_bytes(BODY)
    winapi.CreateJunction(str(real_directory), str(tmp_path / "docs"))
    _set_manifest_field(manifest, ("release", "body_path"), "docs/notes.md")

    assert _prepare_error(manifest, tmp_path) == "manifest body symlink"


@pytest.mark.skipif(os.name != "nt", reason="byte-range locks are Windows-only")
@pytest.mark.parametrize("scope", ("manifest", "body"))
def test_locked_input_read_failure_is_value_safe(tmp_path: Path, scope: str) -> None:
    msvcrt = pytest.importorskip("msvcrt")
    manifest = _write_manifest(tmp_path)
    target = manifest if scope == "manifest" else tmp_path / "notes.md"
    size = target.stat().st_size

    with target.open("r+b") as holder:
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, size)
        try:
            code = _prepare_error(manifest, tmp_path)
        finally:
            holder.seek(0)
            msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, size)

    assert code == f"manifest {scope} read"


@pytest.mark.skipif(os.name != "nt", reason="drive letters are Windows-only")
def test_input_on_another_drive_is_rejected(tmp_path: Path) -> None:
    drive = "Y:" if str(tmp_path).upper().startswith("Z:") else "Z:"
    other_drive = f"{drive}\\manifest.json"

    with pytest.raises(ManifestError) as parts_error:
        publisher._workspace_relative_parts(
            publisher.Path(other_drive), tmp_path, "manifest"
        )

    assert _prepare_error(other_drive, tmp_path) == "manifest manifest path"
    assert parts_error.value.code == "manifest manifest path"


@pytest.mark.parametrize(
    "relative",
    (
        pytest.param((), id="workspace-itself"),
        pytest.param(("..", "outside.bin"), id="parent-escape"),
    ),
)
def test_workspace_relative_parts_require_a_strict_descendant(
    tmp_path: Path, relative: tuple[str, ...]
) -> None:
    workspace = tmp_path / "workspace"
    inside = workspace / "dist" / "a.bin"

    with pytest.raises(ManifestError) as error:
        publisher._workspace_relative_parts(
            workspace.joinpath(*relative), workspace, "asset"
        )

    assert error.value.code == "manifest asset path"
    assert publisher._workspace_relative_parts(inside, workspace, "asset") == (
        "dist",
        "a.bin",
    )


def test_file_metadata_rejects_non_regular_and_replaced_files(tmp_path: Path) -> None:
    path = tmp_path / "asset.bin"
    path.write_bytes(b"asset")
    regular = stat.S_IFREG | 0o644

    with pytest.raises(ManifestError) as directory_error:
        publisher._validate_file_metadata(
            path,
            None,
            _stat(stat.S_IFDIR | 0o755),
            max_size=16,
            expected_size=None,
            scope="asset",
        )
    with pytest.raises(ManifestError) as replaced_error:
        publisher._validate_file_metadata(
            path,
            _stat(regular, ino=1, size=5),
            _stat(regular, ino=2, size=5),
            max_size=16,
            expected_size=5,
            scope="asset",
        )

    assert directory_error.value.code == "manifest asset file"
    assert replaced_error.value.code == "manifest asset changed"
    for before in (None, _stat(regular, ino=1, size=5)):
        assert (
            publisher._validate_file_metadata(
                path,
                before,
                _stat(regular, ino=1, size=5),
                max_size=16,
                expected_size=5,
                scope="asset",
            )
            is None
        )


def test_stream_bounds_reject_growth_after_metadata_check() -> None:
    with pytest.raises(ManifestError) as read_error:
        publisher._read_limited(io.BytesIO(b"x" * 8), limit=4, scope="manifest")
    with pytest.raises(ManifestError) as expected_error:
        publisher._copy_staged(
            io.BytesIO(b"x" * 8),
            io.BytesIO(),
            limits=PublishLimits(),
            expected_size=4,
            scope="asset",
        )
    with pytest.raises(ManifestError) as limit_error:
        publisher._copy_staged(
            io.BytesIO(b"x" * 8),
            io.BytesIO(),
            limits=PublishLimits(max_file_bytes=4),
            expected_size=None,
            scope="body",
        )

    assert read_error.value.code == "manifest manifest size"
    assert expected_error.value.code == "manifest asset size"
    assert limit_error.value.code == "manifest body size"
    assert publisher._read_limited(io.BytesIO(b"x" * 4), limit=4, scope="x") == b"xxxx"


def test_output_identity_rejects_replaced_file() -> None:
    regular = stat.S_IFREG | 0o644

    with pytest.raises(ProviderError) as error:
        publisher._validate_output_identity(
            _stat(regular, ino=7), _stat(regular, ino=8)
        )

    assert error.value.code == "output preflight"
    assert (
        publisher._validate_output_identity(
            _stat(regular, ino=7), _stat(regular, ino=7)
        )
        is None
    )


@pytest.mark.parametrize(
    "mode,uid",
    (
        pytest.param(stat.S_IFLNK | 0o777, 0, id="symlink"),
        pytest.param(stat.S_IFDIR | 0o755, 0, id="directory"),
        pytest.param(stat.S_IFREG | 0o644, 1000, id="non-root-owner"),
        pytest.param(stat.S_IFREG | 0o664, 0, id="group-writable"),
        pytest.param(stat.S_IFREG | 0o646, 0, id="world-writable"),
    ),
)
def test_ca_bundle_must_be_root_owned_read_only_regular_file(
    mode: int, uid: int
) -> None:
    with pytest.raises(ProviderError) as error:
        publisher._validate_ca_bundle(_stat(mode, uid=uid))

    assert error.value.code == "provider ca"
    assert publisher._validate_ca_bundle(_stat(stat.S_IFREG | 0o644, uid=0)) is None


def test_defensive_validators_reject_shapes_json_cannot_produce() -> None:
    with pytest.raises(ManifestError) as key_error:
        publisher._walk_for_controls({1: "x"})
    with pytest.raises(ManifestError) as control_error:
        publisher._require_string("a\tb", "release name")

    assert key_error.value.code == "manifest key"
    assert control_error.value.code == "manifest release name"
    assert publisher._require_string("v1.0.0", "release name") == "v1.0.0"


@pytest.mark.parametrize(
    "body,code",
    (
        pytest.param(b"\xff\xfe", "body encoding", id="encoding"),
        pytest.param(b"notes\x00", "body control character", id="nul"),
    ),
)
def test_invalid_release_body_is_rejected_before_provider_calls(
    tmp_path: Path, github_env: pytest.MonkeyPatch, body: bytes, code: str
) -> None:
    manifest = _write_manifest(tmp_path, body=body)
    provider = FakeProvider()

    with pytest.raises(ManifestError) as error:
        publish_manifest(
            manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
        )

    assert error.value.code == code
    assert provider.calls == []


def test_missing_staged_body_is_never_reread_from_workspace(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()

    with closing(
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)
    ) as prepared:
        prepared.body.path.unlink()
        with pytest.raises(ManifestError) as error:
            reconcile(prepared, provider)

    assert error.value.code == "staged file read"
    assert (tmp_path / "notes.md").read_bytes() == BODY
    assert provider.calls == []


def test_tampered_staged_asset_is_never_uploaded(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()

    with closing(
        prepare_manifest(manifest, tmp_path, expected_repository=REPOSITORY)
    ) as prepared:
        prepared.assets["dist.tar.gz"].path.write_bytes(b"tampered byte")
        with pytest.raises(ManifestError) as error:
            reconcile(prepared, provider)

    assert error.value.code == "staged file changed"
    assert "upload_asset" not in _call_names(provider)


@pytest.mark.parametrize(
    "outcome",
    (
        pytest.param(ConnectionResetError("reset near test-token"), id="transport"),
        pytest.param(RuntimeError("test-token leaked"), id="unexpected"),
        pytest.param(["not", "a", "mapping"], id="non-mapping"),
    ),
)
def test_tag_ref_failures_are_definitive_and_value_safe(
    tmp_path: Path, github_env: pytest.MonkeyPatch, outcome: Any
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()

    def get_tag_ref(_repository: str, tag: str) -> Any:
        provider.calls.append(("get_tag_ref", tag))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    provider.get_tag_ref = get_tag_ref  # type: ignore[method-assign]
    error = _publish_error(manifest, provider)

    assert error.code == "tag ref"
    assert str(error) == "publisher tag ref"
    assert error.__cause__ is None
    assert not isinstance(error, AmbiguousProviderError)
    assert _call_names(provider) == ["get_tag_ref"]


@pytest.mark.parametrize(
    "create_error",
    (
        pytest.param(RuntimeError("test-token"), id="unexpected"),
        pytest.param(ProviderHttpError("create release", status=422), id="http-422"),
        pytest.param(ProviderHttpError("create release", status=503), id="http-503"),
    ),
)
def test_ambiguous_create_without_readback_release_fails_closed(
    tmp_path: Path, github_env: pytest.MonkeyPatch, create_error: BaseException
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    provider.create_error = create_error

    error = _publish_error(manifest, provider)

    assert isinstance(error, AmbiguousProviderError)
    assert error.code == "create release readback"
    assert _call_names(provider) == [
        "get_tag_ref",
        "get_release_by_tag",
        "create_release",
        "get_release_by_tag",
    ]


@pytest.mark.parametrize(
    "create_error",
    (
        pytest.param(ProviderHttpError("create release", status=403), id="http-403"),
        pytest.param(ProviderError("create release"), id="provider-error"),
    ),
)
def test_definitive_create_failure_is_not_read_back(
    tmp_path: Path, github_env: pytest.MonkeyPatch, create_error: ProviderError
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    provider.create_error = create_error

    error = _publish_error(manifest, provider)

    assert error is create_error
    assert _call_names(provider) == [
        "get_tag_ref",
        "get_release_by_tag",
        "create_release",
    ]


def test_unverifiable_create_response_is_confirmed_by_readback(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    original_create = provider.create_release

    def create_with_partial_response(
        repository: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        original_create(repository, payload)
        return {"id": 41}

    provider.create_release = create_with_partial_response  # type: ignore[method-assign]
    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["created"] is True
    assert result["release_id"] == 41
    assert _call_names(provider).count("create_release") == 1
    assert _call_names(provider).count("get_release_by_tag") == 3


def test_peel_tag_accepts_flat_ref_and_normalizes_sha() -> None:
    provider = FakeProvider()
    provider.tag_refs["v1.0.0"] = {"type": "commit", "sha": "A" * 40}

    assert peel_tag(provider, REPOSITORY, "v1.0.0") == "a" * 40


@pytest.mark.parametrize(
    "ref,code",
    (
        pytest.param({"ref": "refs/tags/v1.0.0"}, "tag ref", id="no-object"),
        pytest.param(
            {"object": {"type": 1, "sha": TARGET_SHA}}, "tag response", id="type"
        ),
        pytest.param(
            {"object": {"type": "commit", "sha": "xyz"}}, "tag response", id="sha"
        ),
    ),
)
def test_peel_tag_rejects_malformed_refs(ref: dict[str, Any], code: str) -> None:
    provider = FakeProvider()
    provider.tag_refs["v1.0.0"] = ref

    with pytest.raises(ProviderError) as error:
        peel_tag(provider, REPOSITORY, "v1.0.0")

    assert error.value.code == code


def test_peel_tag_enforces_depth_bound() -> None:
    provider = FakeProvider()
    provider.tag_refs["v1.0.0"] = {"object": {"type": "tag", "sha": "b" * 40}}
    provider.tag_objects["b" * 40] = {"object": {"type": "tag", "sha": "c" * 40}}
    provider.tag_objects["c" * 40] = {"object": {"type": "commit", "sha": TARGET_SHA}}

    with pytest.raises(ProviderError) as zero_error:
        peel_tag(provider, REPOSITORY, "v1.0.0", max_depth=0)
    assert provider.calls == []
    with pytest.raises(ProviderError) as deep_error:
        peel_tag(provider, REPOSITORY, "v1.0.0", max_depth=2)

    assert zero_error.value.code == "tag depth"
    assert deep_error.value.code == "tag depth"
    assert peel_tag(provider, REPOSITORY, "v1.0.0", max_depth=3) == TARGET_SHA


@pytest.mark.parametrize(
    "field,value,code",
    (
        pytest.param("id", 0, "release id", id="id-zero"),
        pytest.param("id", "41", "release id", id="id-string"),
        pytest.param("html_url", None, "release url", id="url-missing"),
        pytest.param(
            "html_url", "http://github.com/o/r/releases", "release url", id="url-http"
        ),
        pytest.param(
            "html_url",
            "https://github.com/o/r/releases?tag=v1.0.0",
            "release url",
            id="url-query",
        ),
        pytest.param(
            "html_url",
            "https://user@github.com/o/r/releases",
            "release url",
            id="url-userinfo",
        ),
    ),
)
def test_existing_release_identity_must_be_well_formed(
    tmp_path: Path, github_env: pytest.MonkeyPatch, field: str, value: Any, code: str
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.releases["v1.0.0"][field] = value

    error = _publish_error(manifest, provider)

    assert error.code == code
    assert _call_names(provider) == ["get_tag_ref", "get_release_by_tag"]


@pytest.mark.parametrize(
    "listing",
    (
        pytest.param({"name": "dist.tar.gz"}, id="object"),
        pytest.param("dist.tar.gz", id="string"),
    ),
)
def test_asset_listing_must_be_a_sequence(
    tmp_path: Path, github_env: pytest.MonkeyPatch, listing: Any
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)

    def list_release_assets(
        _repository: str, _release_id: int, *, page: int, per_page: int
    ) -> Any:
        provider.calls.append(("list_release_assets", page))
        return listing

    provider.list_release_assets = list_release_assets  # type: ignore[method-assign]
    error = _publish_error(manifest, provider)

    assert error.code == "asset page"
    assert "upload_asset" not in _call_names(provider)


def test_asset_listing_beyond_page_bound_fails_closed(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path, assets=[("a.bin", b"a"), ("b.bin", b"b")])
    provider = FakeProvider()
    provider.page_size = 1
    _seed_release(provider, manifest)
    provider.assets["v1.0.0"] = [
        {
            "name": "a.bin",
            "size": 1,
            "digest": "sha256:" + _sha(b"a"),
            "content_type": "application/octet-stream",
        }
    ]

    error = _publish_error(
        manifest, provider, limits=PublishLimits(max_pages=1, asset_page_size=1)
    )

    assert error.code == "asset pagination"
    assert _call_names(provider).count("list_release_assets") == 1
    assert "upload_asset" not in _call_names(provider)


def test_foreign_well_formed_asset_blocks_upload(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.assets["v1.0.0"] = [
        {
            "name": "foreign.bin",
            "size": 1,
            "digest": "sha256:" + _sha(b"x"),
            "content_type": "application/octet-stream",
        }
    ]

    error = _publish_error(manifest, provider)

    assert error.code == "foreign asset"
    assert "upload_asset" not in _call_names(provider)


@pytest.mark.parametrize(
    "latest",
    (
        pytest.param(None, id="no-latest-release"),
        pytest.param({"tag_name": 5}, id="non-string-tag"),
    ),
)
def test_requested_latest_policy_must_be_provable(
    tmp_path: Path, github_env: pytest.MonkeyPatch, latest: dict[str, Any] | None
) -> None:
    manifest = _write_manifest(tmp_path, make_latest=True)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.latest = latest

    error = _publish_error(manifest, provider)

    assert error.code == "latest policy"
    assert "upload_asset" not in _call_names(provider)


def test_unparseable_upload_response_converges_by_relisting(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    original_upload = provider.upload_asset

    def upload_with_empty_response(
        repository: str, release_id: int, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        original_upload(repository, release_id, name, content_type, data)
        return {}

    provider.upload_asset = upload_with_empty_response  # type: ignore[method-assign]
    result = publish_manifest(
        manifest, workspace=tmp_path, provider=provider, repository=REPOSITORY
    )

    assert result["uploaded_count"] == 1
    assert _call_names(provider).count("upload_asset") == 1
    assert _call_names(provider).count("list_release_assets") == 3


def test_unpersisted_upload_with_unparseable_response_fails_closed(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)

    def upload_without_storing(
        repository: str, release_id: int, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        provider.calls.append(("upload_asset", name))
        return {}

    provider.upload_asset = upload_without_storing  # type: ignore[method-assign]
    error = _publish_error(manifest, provider)

    assert isinstance(error, AmbiguousProviderError)
    assert error.code == "upload readback"
    assert _call_names(provider).count("upload_asset") == 1


@pytest.mark.parametrize(
    "status,code,listings",
    (
        pytest.param(403, "upload asset", 1, id="definitive-403"),
        pytest.param(503, "upload readback", 2, id="ambiguous-503"),
    ),
)
def test_upload_http_failure_relists_only_when_ambiguous(
    tmp_path: Path,
    github_env: pytest.MonkeyPatch,
    status: int,
    code: str,
    listings: int,
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    provider.upload_errors["dist.tar.gz"] = ProviderHttpError(
        "upload asset", status=status
    )

    error = _publish_error(manifest, provider)

    assert error.code == code
    assert _call_names(provider).count("upload_asset") == 1
    assert _call_names(provider).count("list_release_assets") == listings


@pytest.mark.parametrize(
    "change,code",
    (
        pytest.param("release-removed", "release post-read", id="release-removed"),
        pytest.param("release-replaced", "release identity", id="release-replaced"),
        pytest.param("asset-lost", "asset set", id="asset-lost"),
    ),
)
def test_post_write_state_change_is_detected(
    tmp_path: Path, github_env: pytest.MonkeyPatch, change: str, code: str
) -> None:
    manifest = _write_manifest(tmp_path)
    provider = FakeProvider()
    _seed_release(provider, manifest)
    original_upload = provider.upload_asset
    changes: dict[str, Callable[[], object]] = {
        "release-removed": lambda: provider.releases.pop("v1.0.0"),
        "release-replaced": lambda: provider.releases["v1.0.0"].update(id=42),
        "asset-lost": lambda: provider.assets["v1.0.0"].clear(),
    }

    def upload_then_change(
        repository: str, release_id: int, name: str, content_type: str, data: bytes
    ) -> dict[str, Any]:
        item = original_upload(repository, release_id, name, content_type, data)
        changes[change]()
        return item

    provider.upload_asset = upload_then_change  # type: ignore[method-assign]
    error = _publish_error(manifest, provider)

    assert error.code == code
    assert _call_names(provider).count("upload_asset") == 1


def _result_values() -> dict[str, Any]:
    return {key: f"value-{index}" for index, key in enumerate(publisher._RESULT_KEYS)}


def test_write_outputs_appends_every_result_key_in_order(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github-output"
    output.write_bytes(b"existing=1\n")
    github_env.setenv("GITHUB_OUTPUT", str(output))
    values = _result_values()

    publisher._write_outputs(values)

    assert output.read_bytes().decode("utf-8") == "existing=1\n" + "".join(
        f"{key}={values[key]}\n" for key in publisher._RESULT_KEYS
    )
    output.unlink()
    github_env.delenv("GITHUB_OUTPUT")
    publisher._write_outputs(values)
    assert not output.exists()


def test_closed_output_channel_refuses_writes(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "github-output"
    output.write_bytes(b"")
    github_env.setenv("GITHUB_OUTPUT", str(output))
    channel = publisher._open_output_channel()
    assert channel is not None

    channel.close()
    channel.close()
    with pytest.raises(ProviderError) as error:
        channel.write(_result_values())

    assert error.value.code == "output write"
    assert output.read_bytes() == b""


def test_output_write_failure_is_value_safe(tmp_path: Path) -> None:
    output = tmp_path / "github-output"
    output.write_bytes(b"")
    channel = publisher._OutputChannel(
        os.open(output, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    )

    try:
        with pytest.raises(ProviderError) as error:
            channel.write(_result_values())
    finally:
        channel.close()

    assert error.value.code == "output write"
    assert error.value.__cause__ is None
    assert output.read_bytes() == b""


def test_missing_output_file_fails_before_provider_use(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    github_env.setenv("GITHUB_OUTPUT", str(tmp_path / "missing-output"))
    provider = FakeProvider()

    error = _publish_error(manifest, provider)

    assert error.code == "output preflight"
    assert provider.calls == []


def test_publish_manifest_releases_its_own_output_channel(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    output = tmp_path / "github-output"
    output.write_bytes(b"")
    github_env.setenv("GITHUB_OUTPUT", str(output))

    result = publish_manifest(
        manifest, workspace=tmp_path, provider=FakeProvider(), repository=REPOSITORY
    )

    assert result["uploaded_count"] == 1
    assert output.read_bytes() == b""
    # Windows refuses to delete a file with an open handle, so this also proves
    # the publisher-owned descriptor was released.
    output.unlink()
    assert not output.exists()


def test_missing_token_fails_before_provider_construction(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)

    with pytest.raises(ProviderError) as error:
        publish_manifest(manifest, workspace=tmp_path, repository=REPOSITORY)

    assert error.value.code == "token input"


def test_provider_factory_failure_is_value_safe(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    seen_tokens: list[str] = []

    def provider_factory(token: str) -> FakeProvider:
        seen_tokens.append(token)
        raise RuntimeError("factory rejected " + token)

    with pytest.raises(ProviderError) as error:
        publish_manifest(
            manifest,
            workspace=tmp_path,
            provider_factory=provider_factory,
            token="test-token",
            repository=REPOSITORY,
        )

    assert error.value.code == "provider init"
    assert "test-token" not in str(error.value)
    assert error.value.__cause__ is None
    assert seen_tokens == ["test-token"]


def test_provider_factory_publisher_error_is_preserved(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)
    raised = ProviderError("provider ca")

    def provider_factory(token: str) -> FakeProvider:
        raise raised

    with pytest.raises(ProviderError) as error:
        publish_manifest(
            manifest,
            workspace=tmp_path,
            provider_factory=provider_factory,
            token="test-token",
            repository=REPOSITORY,
        )

    assert error.value is raised


def test_default_provider_rejects_control_characters_in_token(
    tmp_path: Path, github_env: pytest.MonkeyPatch
) -> None:
    manifest = _write_manifest(tmp_path)

    with pytest.raises(ProviderError) as error:
        publish_manifest(
            manifest, workspace=tmp_path, token="test\ntoken", repository=REPOSITORY
        )

    assert error.value.code == "token input"
    assert str(error.value) == "publisher token input"


class _BytesResponse:
    def __init__(self, status: int, data: bytes) -> None:
        self.status = status
        self._stream = io.BytesIO(data)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def close(self) -> None:
        self.closed = True


class _StaticOpener:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.urls: list[str] = []

    def build(self, *_handlers: Any) -> _StaticOpener:
        return self

    def open(self, request: Any, *, timeout: float) -> Any:
        del timeout
        self.urls.append(request.full_url)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _not_found(url: str) -> Any:
    return publisher.urllib.error.HTTPError(
        url, 404, "Not Found", None, io.BytesIO(b'{"message":"Not Found"}')
    )


def _install_opener(monkeypatch: pytest.MonkeyPatch, opener: Any) -> None:
    monkeypatch.setattr(
        publisher, "_github_ssl_context", publisher.ssl.create_default_context
    )
    monkeypatch.setattr(publisher.urllib.request, "build_opener", opener.build)


@pytest.mark.parametrize(
    "failure",
    (
        pytest.param(publisher.urllib.error.URLError("unreachable"), id="url-error"),
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(ConnectionResetError(), id="reset"),
    ),
)
def test_http_transport_failure_is_reported_as_transport_error(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    opener = _StaticOpener(failure)
    _install_opener(monkeypatch, opener)

    with pytest.raises(ProviderTransportError) as error:
        HttpGithubProvider("test-token").get_tag_ref(REPOSITORY, "v1.0.0")

    assert error.value.code == "tag ref"
    assert opener.urls == [f"{_API}/git/ref/tags/v1.0.0"]


def test_http_unexpected_success_status_on_read_is_definitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _BytesResponse(204, b"")
    _install_opener(monkeypatch, _StaticOpener(response))

    with pytest.raises(ProviderHttpError) as error:
        HttpGithubProvider("test-token").get_tag_ref(REPOSITORY, "v1.0.0")

    assert error.value.status == 204
    assert error.value.code == "tag ref"
    assert response.closed is True


def test_http_conflict_on_read_is_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    conflict = publisher.urllib.error.HTTPError(
        f"{_API}/git/ref/tags/v1.0.0", 409, "Conflict", None, io.BytesIO(b"{}")
    )
    _install_opener(monkeypatch, _StaticOpener(conflict))

    with pytest.raises(AmbiguousProviderError) as error:
        HttpGithubProvider("test-token").get_tag_ref(REPOSITORY, "v1.0.0")

    assert error.value.status == 409
    assert error.value.code == "tag ref"


def test_http_missing_release_reads_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _StaticOpener(_not_found(f"{_API}/releases/tags/v1.0.0"))
    _install_opener(monkeypatch, opener)

    release = HttpGithubProvider("test-token").get_release_by_tag(REPOSITORY, "v1.0.0")

    assert release is None
    assert opener.urls == [f"{_API}/releases/tags/v1.0.0"]


def test_http_non_json_read_response_is_not_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _BytesResponse(200, b"<html>")
    opener = _StaticOpener(response)
    _install_opener(monkeypatch, opener)

    with pytest.raises(ProviderError) as error:
        HttpGithubProvider("test-token").get_tag_object(REPOSITORY, "b" * 40)

    assert error.value.code == "response json"
    assert not isinstance(error.value, AmbiguousProviderError)
    assert opener.urls == [f"{_API}/git/tags/{'b' * 40}"]
    assert response.closed is True


def test_http_provider_validates_inputs_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _StaticOpener(_BytesResponse(200, b"{}"))
    _install_opener(monkeypatch, opener)
    provider = HttpGithubProvider("test-token")

    with pytest.raises(ProviderError) as repository_error:
        provider.get_tag_ref("not a repository", "v1.0.0")
    with pytest.raises(ProviderError) as page_error:
        provider.list_release_assets(REPOSITORY, 41, page=0, per_page=100)

    assert repository_error.value.code == "repository"
    assert page_error.value.code == "asset pagination"
    assert opener.urls == []


def test_http_asset_listing_must_be_an_array(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _StaticOpener(_BytesResponse(200, b'{"name":"dist.tar.gz"}'))
    _install_opener(monkeypatch, opener)

    with pytest.raises(ProviderError) as error:
        HttpGithubProvider("test-token").list_release_assets(
            REPOSITORY, 41, page=2, per_page=50
        )

    assert error.value.code == "asset page"
    assert opener.urls == [f"{_API}/releases/41/assets?page=2&per_page=50"]


def test_redirect_handler_follows_bounded_upload_redirect() -> None:
    handler = publisher._SafeRedirectHandler(max_redirects=1)
    request = publisher.urllib.request.Request(f"{_API}/releases/41/assets")
    target = f"{_UPLOADS}/releases/41/assets"
    first = _TrackedResponse()
    second = _TrackedResponse()

    redirected = handler.redirect_request(request, first, 302, "Found", {}, target)
    assert redirected is not None
    with pytest.raises(ProviderError) as error:
        handler.redirect_request(redirected, second, 302, "Found", {}, target)

    assert redirected.full_url == target
    assert first.closed is False
    assert error.value.code == "redirect bound"
    assert second.closed is True


def test_redirect_handler_rejects_credentials_in_target() -> None:
    handler = publisher._SafeRedirectHandler(max_redirects=3)
    request = publisher.urllib.request.Request(f"{_API}/releases")
    response = _TrackedResponse()

    with pytest.raises(ProviderError) as error:
        handler.redirect_request(
            request,
            response,
            302,
            "Found",
            {},
            "https://user@uploads.github.com/repos/o/r/releases",
        )

    assert error.value.code == "provider url"
    assert response.closed is True


@pytest.mark.skipif(
    os.name != "nt" and not os.path.isfile(publisher._CA_BUNDLE_PATH),
    reason="the pinned CA bundle is not installed",
)
def test_github_ssl_context_enforces_verified_tls() -> None:
    context = publisher._github_ssl_context()

    assert context.verify_mode == publisher.ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= publisher.ssl.TLSVersion.TLSv1_2


@pytest.mark.skipif(os.name == "nt", reason="Windows uses the system certificate store")
@pytest.mark.parametrize("bundle", ("missing", "world-writable"))
def test_github_ssl_context_rejects_unsafe_ca_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bundle: str
) -> None:
    bundle_path = tmp_path / "ca-certificates.crt"
    if bundle == "world-writable":
        bundle_path.write_bytes(b"")
        bundle_path.chmod(0o666)
    monkeypatch.setattr(publisher, "_CA_BUNDLE_PATH", str(bundle_path))

    with pytest.raises(ProviderError) as error:
        publisher._github_ssl_context()

    assert error.value.code == "provider ca"


class _FakeGithubApi:
    """Stateful GitHub REST double installed at the urllib opener boundary."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.authorizations: set[str | None] = set()
        self.release: dict[str, Any] | None = None
        self.assets: list[dict[str, Any]] = []
        self.latest_tag: str | None = None

    def build(self, *_handlers: Any) -> _FakeGithubApi:
        return self

    def open(self, request: Any, *, timeout: float) -> _BytesResponse:
        del timeout
        method = request.get_method()
        self.requests.append((method, request.full_url))
        self.authorizations.add(request.get_header("Authorization"))
        parsed = publisher.urllib.parse.urlsplit(request.full_url)
        path = parsed.path.removeprefix(f"/repos/{REPOSITORY}")
        routes: dict[tuple[str, str | None, str], Callable[..., _BytesResponse]] = {
            ("GET", "api.github.com", "/git/ref/tags/v1.0.0"): self._tag_ref,
            ("GET", "api.github.com", "/git/tags/" + "b" * 40): self._tag_object,
            ("GET", "api.github.com", "/releases/tags/v1.0.0"): self._release_by_tag,
            ("POST", "api.github.com", "/releases"): self._create_release,
            ("GET", "api.github.com", "/releases/41/assets"): self._list_assets,
            ("POST", "uploads.github.com", "/releases/41/assets"): self._upload_asset,
            ("GET", "api.github.com", "/releases/latest"): self._latest_release,
        }
        return routes[(method, parsed.hostname, path)](request, parsed)

    @staticmethod
    def _json(status: int, value: Any) -> _BytesResponse:
        return _BytesResponse(status, json.dumps(value).encode("utf-8"))

    def _tag_ref(self, _request: Any, _parsed: Any) -> _BytesResponse:
        return self._json(
            200,
            {"ref": "refs/tags/v1.0.0", "object": {"type": "tag", "sha": "b" * 40}},
        )

    def _tag_object(self, _request: Any, _parsed: Any) -> _BytesResponse:
        return self._json(
            200, {"sha": "b" * 40, "object": {"type": "commit", "sha": TARGET_SHA}}
        )

    def _release_by_tag(self, request: Any, _parsed: Any) -> _BytesResponse:
        if self.release is None:
            raise _not_found(request.full_url)
        return self._json(200, self.release)

    def _create_release(self, request: Any, _parsed: Any) -> _BytesResponse:
        payload = json.loads(request.data)
        self.release = {
            "id": 41,
            "html_url": (
                f"https://github.com/{REPOSITORY}/releases/tag/{payload['tag_name']}"
            ),
            "tag_name": payload["tag_name"],
            "name": payload["name"],
            "body": payload["body"],
            "draft": payload["draft"],
            "prerelease": payload["prerelease"],
        }
        if payload["make_latest"] == "true":
            self.latest_tag = payload["tag_name"]
        return self._json(201, self.release)

    def _list_assets(self, _request: Any, parsed: Any) -> _BytesResponse:
        query = publisher.urllib.parse.parse_qs(parsed.query)
        per_page = int(query["per_page"][0])
        start = (int(query["page"][0]) - 1) * per_page
        return self._json(200, self.assets[start : start + per_page])

    def _upload_asset(self, request: Any, parsed: Any) -> _BytesResponse:
        query = publisher.urllib.parse.parse_qs(parsed.query)
        data = request.data
        item = {
            "name": query["name"][0],
            "size": len(data),
            "digest": "sha256:" + _sha(data),
            "content_type": request.get_header("Content-type"),
        }
        self.assets.append(item)
        return self._json(201, item)

    def _latest_release(self, request: Any, _parsed: Any) -> _BytesResponse:
        if self.latest_tag is None:
            raise _not_found(request.full_url)
        return self._json(200, {"tag_name": self.latest_tag})


@pytest.mark.parametrize("make_latest", (False, True))
@pytest.mark.parametrize("with_output", (False, True))
def test_main_publishes_through_github_rest_boundary(
    tmp_path: Path,
    github_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    make_latest: bool,
    with_output: bool,
) -> None:
    _write_manifest(tmp_path, make_latest=make_latest)
    api = _FakeGithubApi()
    _install_opener(github_env, api)
    github_env.setenv("INPUT_MANIFEST", "manifest.json")
    github_env.setenv("GITHUB_WORKSPACE", str(tmp_path))
    github_env.setenv("GITHUB_REPOSITORY", REPOSITORY)
    github_env.setenv("INPUT_TOKEN", "test-token")
    output = tmp_path / "github-output"
    if with_output:
        output.write_bytes(b"")
        github_env.setenv("GITHUB_OUTPUT", str(output))

    exit_code = publisher.main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "test-token" not in captured.out
    result = json.loads(captured.out)
    assert list(result) == list(publisher._RESULT_KEYS)
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    assert result["result_sha256"] == _sha(
        json.dumps(unsigned, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    assert (
        result["release_id"],
        result["release_url"],
        result["created"],
        result["uploaded_count"],
        result["reused_count"],
    ) == (41, f"https://github.com/{REPOSITORY}/releases/tag/v1.0.0", True, 1, 0)
    asset_list = f"{_API}/releases/41/assets?page=1&per_page=100"
    tag_reads = [
        ("GET", f"{_API}/git/ref/tags/v1.0.0"),
        ("GET", f"{_API}/git/tags/{'b' * 40}"),
    ]
    assert api.requests == [
        *tag_reads,
        ("GET", f"{_API}/releases/tags/v1.0.0"),
        ("POST", f"{_API}/releases"),
        ("GET", f"{_API}/releases/latest"),
        ("GET", asset_list),
        ("POST", f"{_UPLOADS}/releases/41/assets?name=dist.tar.gz"),
        *tag_reads,
        ("GET", f"{_API}/releases/tags/v1.0.0"),
        ("GET", asset_list),
        ("GET", f"{_API}/releases/latest"),
    ]
    assert api.authorizations == {"Bearer test-token"}
    assert api.assets[0]["digest"] == "sha256:" + _sha(b"package bytes")
    if with_output:
        assert output.read_bytes().decode("utf-8") == "".join(
            f"{key}={result[key]}\n" for key in publisher._RESULT_KEYS
        )
        output.unlink()
    assert not output.exists()


def test_main_requires_manifest_input(
    github_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = publisher.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "publisher manifest input\n"


def test_main_requires_repository_input(
    tmp_path: Path,
    github_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github_env.setenv("GITHUB_WORKSPACE", str(tmp_path))

    exit_code = publisher.main(["--manifest", "manifest.json"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "publisher repository input\n"


def test_main_hides_unexpected_failures_and_closes_output(
    tmp_path: Path,
    github_env: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_manifest(tmp_path)
    output = tmp_path / "github-output"
    output.write_bytes(b"")
    github_env.setenv("INPUT_MANIFEST", "manifest.json")
    github_env.setenv("GITHUB_WORKSPACE", str(tmp_path))
    github_env.setenv("GITHUB_REPOSITORY", REPOSITORY)
    github_env.setenv("INPUT_TOKEN", "test-token")
    github_env.setenv("GITHUB_OUTPUT", str(output))
    # A runner whose temporary directory vanished cannot stage inputs.
    github_env.setattr(publisher.tempfile, "tempdir", str(tmp_path / "missing-tmp"))

    exit_code = publisher.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "publisher failed\n"
    assert output.read_bytes() == b""
    output.unlink()
    assert not output.exists()


def test_module_entry_point_exits_with_main_status(
    github_env: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    github_env.setattr(sys, "argv", ["bsr-publisher"])
    github_env.delitem(sys.modules, publisher.__name__)

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module(publisher.__name__, run_name="__main__", alter_sys=True)

    assert exit_info.value.code == 1
    assert capsys.readouterr().err == "publisher manifest input\n"
