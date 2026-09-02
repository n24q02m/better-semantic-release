from __future__ import annotations

import hashlib
import json
import os
from contextlib import closing
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from semantic_release.bsr import release_publisher as publisher
from semantic_release.bsr.release_publisher import (
    AmbiguousProviderError,
    HttpGithubProvider,
    ManifestError,
    ProviderError,
    PublishLimits,
    prepare_manifest,
    publish_manifest,
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
