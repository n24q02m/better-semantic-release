from __future__ import annotations

import base64
import copy
import hashlib
import io
import itertools
import json
import re
import runpy
import sys
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from typing_extensions import Self

import pytest

from semantic_release.bsr import action_pin_registry as registry_module
from semantic_release.bsr.action_pin_registry import (
    RegistryError,
    build_record,
    canonical_record_bytes,
    load_remote_chain_head,
    load_remote_registry,
    select_latest,
    verify_record,
)

HEAD = "a" * 40
REPOSITORY = "n24q02m/better-semantic-release"
NOW = datetime(2026, 8, 30, 1, 0, tzinfo=timezone.utc)
IMAGE_DIGEST = "sha256:" + "3" * 64
ACTION_BYTES = {
    "action.yml": b"name: BSR\nruns:\n  using: node20\n  main: action.js\n",
    "publish-action/action.yml": (
        b"name: BSR publisher\nruns:\n  using: docker\n"
        b'  image: "docker://ghcr.io/n24q02m/'
        b"better-semantic-release-publisher@" + IMAGE_DIGEST.encode() + b'"\n'
    ),
    "registry-action/action.yml": (
        b"name: BSR registry loader\nruns:\n  using: composite\n  steps: []\n"
    ),
    ".github/workflows/cd.yml": b"name: cd\n",
    "publish-action/Dockerfile": b"FROM python:3.13-slim@sha256:fixture\n",
    "src/semantic_release/bsr/release_publisher.py": b"def main():\n    return 0\n",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_oid(index: int) -> str:
    return f"{index:x}" * 40


def _base_record() -> dict[str, Any]:
    return {
        "schema_version": "bsr-action-registry/v1",
        "generation": 1,
        "phase": "G1",
        "channel": "beta",
        "issued_at": "2026-08-30T00:00:00Z",
        "expires_at": "2026-09-06T00:00:00Z",
        "owner": "release-security",
        "previous": None,
        "head_oid": HEAD,
        "head_hash": "b" * 64,
        "version_action": {
            "repository": REPOSITORY,
            "path": "action.yml",
            "sha": HEAD,
            "action_blob_oid": _blob_oid(10),
            "action_sha256": _sha256(ACTION_BYTES["action.yml"]),
        },
        "publish_action": {
            "repository": REPOSITORY,
            "path": "publish-action/action.yml",
            "sha": HEAD,
            "action_blob_oid": _blob_oid(11),
            "action_sha256": _sha256(ACTION_BYTES["publish-action/action.yml"]),
        },
        "registry_action": {
            "repository": REPOSITORY,
            "path": "registry-action/action.yml",
            "sha": HEAD,
            "action_blob_oid": _blob_oid(12),
            "action_sha256": _sha256(ACTION_BYTES["registry-action/action.yml"]),
        },
        "publisher": {
            "identity": (
                "https://github.com/n24q02m/better-semantic-release/"
                ".github/workflows/cd.yml@refs/heads/main"
            ),
            "image_ref": ("ghcr.io/n24q02m/better-semantic-release-publisher"),
            "image_digest": IMAGE_DIGEST,
            "image_source_commit": HEAD,
            "dockerfile_sha256": _sha256(ACTION_BYTES["publish-action/Dockerfile"]),
            "runtime_sha256": _sha256(
                ACTION_BYTES["src/semantic_release/bsr/release_publisher.py"]
            ),
            "provenance_repository": REPOSITORY,
            "provenance_workflow": ".github/workflows/cd.yml",
            "provenance_attestation_id": "937309",
            "provenance_digest": "8" * 64,
        },
        "advertisement": {
            "repository": REPOSITORY,
            "tag": "v1.6.0-beta.1",
            "target_sha": HEAD,
            "release_url": (
                "https://github.com/n24q02m/better-semantic-release/"
                "releases/tag/v1.6.0-beta.1"
            ),
            "registry_asset": "bsr-action-registry.json",
            "signature_asset": "bsr-action-registry.sigstore.json",
            "image_ref": (
                "ghcr.io/n24q02m/better-semantic-release-publisher@" + IMAGE_DIGEST
            ),
        },
        "workflow": {
            "repository": REPOSITORY,
            "path": ".github/workflows/cd.yml",
            "release_commit": HEAD,
            "blob_oid": _blob_oid(13),
            "sha256": _sha256(ACTION_BYTES[".github/workflows/cd.yml"]),
            "environment": "beta-publish",
        },
    }


def test_build_and_verify_canonical_record() -> None:
    record = build_record(_base_record())

    assert record["record_hash"] == (
        "15dfa19aaaef9af4968e98516fa61b72bff33b0eb34c4621b4f18c7736ce2ee5"
    )
    assert record["result_hash"] == (
        "4b0e3b7ef6c18edb6a38c8897a0c1b7e0a0bb9a8c84c9f67703d36c7491eb2c6"
    )
    assert record["result_oid"] == HEAD

    raw = canonical_record_bytes(record)
    assert raw.endswith(b"\n")
    assert verify_record(raw, now=NOW) == record


def test_record_rejects_publisher_image_outside_bound_repository() -> None:
    unbound = _base_record()
    unbound["publisher"]["image_ref"] = "ghcr.io/attacker/publisher"
    unbound["advertisement"]["image_ref"] = "ghcr.io/attacker/publisher@" + IMAGE_DIGEST

    with pytest.raises(RegistryError, match="publisher image ref"):
        build_record(unbound)


def test_verify_rejects_noncanonical_and_duplicate_keys() -> None:
    record = build_record(_base_record())
    noncanonical = json.dumps(record, indent=2).encode() + b"\n"

    with pytest.raises(RegistryError, match="canonical"):
        verify_record(noncanonical, now=NOW)

    duplicate = b'{"schema_version":"bsr-action-registry/v1","schema_version":"x"}\n'
    with pytest.raises(RegistryError, match="duplicate"):
        verify_record(duplicate, now=NOW)


def test_select_latest_rejects_competing_generation() -> None:
    first = build_record(_base_record())
    competing = dict(first)
    competing["owner"] = "another-owner"
    competing = build_record(
        {
            key: value
            for key, value in competing.items()
            if key not in {"record_hash", "result_oid", "result_hash"}
        }
    )

    with pytest.raises(RegistryError, match="competing generation"):
        select_latest([first, competing], now=NOW)


def test_select_latest_requires_exact_previous_chain() -> None:
    first = build_record(_base_record())
    second_base = _base_record()
    second_base.update(
        {
            "generation": 2,
            "phase": "G2",
            "channel": "stable",
            "issued_at": "2026-08-30T00:30:00Z",
            "expires_at": "2026-09-07T00:00:00Z",
            "previous": {
                "generation": 1,
                "head_oid": HEAD,
                "record_hash": "0" * 64,
                "result_oid": HEAD,
                "result_hash": first["result_hash"],
                "advertisement_tag": first["advertisement"]["tag"],
            },
            "advertisement": {
                **second_base["advertisement"],
                "tag": "v1.6.0",
                "release_url": (
                    "https://github.com/n24q02m/better-semantic-release/"
                    "releases/tag/v1.6.0"
                ),
            },
            "workflow": {
                **second_base["workflow"],
                "environment": "stable-publish",
            },
        }
    )
    second = build_record(second_base)

    with pytest.raises(RegistryError, match="previous"):
        select_latest([first, second], now=NOW)


def test_generation_after_root_requires_previous_record() -> None:
    orphan = _base_record()
    orphan["generation"] = 2

    with pytest.raises(RegistryError, match="previous"):
        build_record(orphan)


def test_select_latest_allows_expired_history_when_latest_is_current() -> None:
    first_base = _base_record()
    first_base.update(
        {
            "issued_at": "2026-08-01T00:00:00Z",
            "expires_at": "2026-08-02T00:00:00Z",
        }
    )
    first = build_record(first_base)
    second_base = _base_record()
    second_base.update(
        {
            "generation": 2,
            "phase": "G2",
            "channel": "stable",
            "issued_at": "2026-08-30T00:30:00Z",
            "expires_at": "2026-09-07T00:00:00Z",
            "previous": {
                "generation": first["generation"],
                "head_oid": first["head_oid"],
                "record_hash": first["record_hash"],
                "result_oid": first["result_oid"],
                "result_hash": first["result_hash"],
                "advertisement_tag": first["advertisement"]["tag"],
            },
            "advertisement": {
                **second_base["advertisement"],
                "tag": "v1.6.0",
                "release_url": (
                    "https://github.com/n24q02m/better-semantic-release/"
                    "releases/tag/v1.6.0"
                ),
            },
            "workflow": {
                **second_base["workflow"],
                "environment": "stable-publish",
            },
        }
    )
    second = build_record(second_base)

    assert select_latest([first, second], now=NOW) == second


def test_cosign_uses_full_workflow_san(monkeypatch: pytest.MonkeyPatch) -> None:
    record = build_record(_base_record())
    calls: list[list[str]] = []

    class _Completed:
        returncode = 0

    def fake_run(command: list[str], **_kwargs: Any) -> _Completed:
        calls.append(command)
        return _Completed()

    monkeypatch.setattr(registry_module.subprocess, "run", fake_run)
    registry_module.verify_with_cosign(
        b"{}\n",
        b"{}",
        identity=record["publisher"]["identity"],
    )

    assert len(calls) == 1
    identity_index = calls[0].index("--certificate-identity")
    assert calls[0][identity_index + 1] == (
        "https://github.com/n24q02m/better-semantic-release/"
        ".github/workflows/cd.yml@refs/heads/main"
    )
    issuer_index = calls[0].index("--certificate-oidc-issuer")
    assert calls[0][issuer_index + 1] == ("https://token.actions.githubusercontent.com")


class _Provider:
    def __init__(self, record_bytes: bytes) -> None:
        self.record_bytes = record_bytes
        self.blobs = {
            path: {"oid": _blob_oid(index), "bytes": content}
            for index, (path, content) in enumerate(ACTION_BYTES.items(), start=10)
        }
        self.release_pages: list[int] = []

    def list_releases(
        self, repository: str, *, page: int, per_page: int
    ) -> list[dict[str, Any]]:
        assert repository == REPOSITORY
        assert per_page == 100
        self.release_pages.append(page)
        if page == 1:
            return [
                {
                    "id": 77,
                    "tag_name": "v1.6.0-beta.1",
                    "html_url": (
                        "https://github.com/n24q02m/better-semantic-release/"
                        "releases/tag/v1.6.0-beta.1"
                    ),
                }
            ]
        return []

    def list_release_assets(
        self, repository: str, release_id: int, *, page: int, per_page: int
    ) -> list[dict[str, Any]]:
        assert repository == REPOSITORY
        assert release_id == 77
        assert per_page == 100
        if page == 1:
            return [
                {"name": "bsr-action-registry.json", "url": "asset://record"},
                {
                    "name": "bsr-action-registry.sigstore.json",
                    "url": "asset://bundle",
                },
            ]
        return []

    def download_asset(self, url: str) -> bytes:
        return self.record_bytes if url == "asset://record" else b"bundle"

    def peel_tag(self, repository: str, tag: str) -> str:
        assert repository == REPOSITORY
        assert tag == "v1.6.0-beta.1"
        return HEAD

    def read_blob(self, repository: str, path: str, sha: str) -> dict[str, Any]:
        assert repository == REPOSITORY
        assert sha == HEAD
        return self.blobs[path]

    def read_oci_manifest(self, image_ref: str, image_digest: str) -> str:
        assert image_ref.endswith("better-semantic-release-publisher")
        return image_digest

    def read_attestation(
        self, repository: str, subject_digest: str, attestation_id: str
    ) -> str:
        assert repository == REPOSITORY
        assert subject_digest == IMAGE_DIGEST
        assert attestation_id == "937309"
        return "8" * 64


def test_chain_head_verifies_latest_signed_record_without_fresh_reads() -> None:
    record = build_record(_base_record())
    raw = canonical_record_bytes(record)
    provider = _Provider(raw)
    verified: list[tuple[bytes, bytes]] = []

    head = load_remote_chain_head(
        provider=provider,
        signature_verifier=lambda payload, bundle: verified.append((payload, bundle)),
        repository=REPOSITORY,
        now=NOW,
    )

    assert head == record
    assert provider.release_pages == [1, 2]
    assert verified == [(raw, b"bundle")]


def test_chain_head_returns_none_only_for_clean_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    monkeypatch.setattr(provider, "list_releases", lambda *_args, **_kwargs: [])

    assert (
        load_remote_chain_head(
            provider=provider,
            signature_verifier=lambda _payload, _bundle: None,
            repository=REPOSITORY,
            now=NOW,
        )
        is None
    )


def test_chain_head_excludes_current_target_for_idempotent_retry() -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    verified: list[tuple[bytes, bytes]] = []

    assert (
        load_remote_chain_head(
            provider=provider,
            signature_verifier=lambda payload, bundle: verified.append(
                (payload, bundle)
            ),
            repository=REPOSITORY,
            excluded_tag="v1.6.0-beta.1",
            now=NOW,
        )
        is None
    )
    assert provider.release_pages == [1, 2]
    assert verified == []


def test_loader_fresh_reads_all_bound_surfaces_and_emits_stable_hash() -> None:
    record = build_record(_base_record())
    raw = canonical_record_bytes(record)
    provider = _Provider(raw)
    verified: list[tuple[bytes, bytes]] = []

    output = load_remote_registry(
        expected=record,
        provider=provider,
        signature_verifier=lambda payload, bundle: verified.append((payload, bundle)),
        repository=REPOSITORY,
        now=NOW,
    )

    assert provider.release_pages == [1, 2]
    assert verified == [(raw, b"bundle")]
    assert output["generation"] == 1
    assert output["head_oid"] == HEAD
    assert output["publisher_image_digest"] == IMAGE_DIGEST
    assert output["loader_output_sha256"] == (
        "8baf118b99317de8acaa0cd629edbe3f7aa49a877e91e30e3112555ee877fce8"
    )


def test_loader_decodes_contents_api_wrapped_base64() -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    blob = provider.blobs["action.yml"]
    encoded = base64.b64encode(blob["bytes"]).decode("ascii")
    provider.blobs["action.yml"] = {
        "sha": blob["oid"],
        "encoding": "base64",
        "content": "\r\n".join(
            encoded[offset : offset + 12] for offset in range(0, len(encoded), 12)
        ),
    }

    output = load_remote_registry(
        expected=record,
        provider=provider,
        signature_verifier=lambda _payload, _bundle: None,
        repository=REPOSITORY,
        now=NOW,
    )

    assert output["head_oid"] == HEAD


@pytest.mark.parametrize("separator", [" ", "\t"])
def test_loader_rejects_non_crlf_base64_whitespace(separator: str) -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    blob = provider.blobs["action.yml"]
    encoded = base64.b64encode(blob["bytes"]).decode("ascii")
    provider.blobs["action.yml"] = {
        "sha": blob["oid"],
        "encoding": "base64",
        "content": f"{encoded[:12]}{separator}{encoded[12:]}",
    }

    with pytest.raises(RegistryError, match="blob content"):
        load_remote_registry(
            expected=record,
            provider=provider,
            signature_verifier=lambda _payload, _bundle: None,
            repository=REPOSITORY,
            now=NOW,
        )


def test_loader_rejects_moved_action_blob() -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    provider.blobs["publish-action/action.yml"]["oid"] = "9" * 40

    with pytest.raises(RegistryError, match="blob"):
        load_remote_registry(
            expected=record,
            provider=provider,
            signature_verifier=lambda _payload, _bundle: None,
            repository=REPOSITORY,
            now=NOW,
        )


def test_loader_rejects_changed_publisher_source() -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    provider.blobs["publish-action/Dockerfile"]["bytes"] = b"changed"

    with pytest.raises(RegistryError, match="publisher source"):
        load_remote_registry(
            expected=record,
            provider=provider,
            signature_verifier=lambda _payload, _bundle: None,
            repository=REPOSITORY,
            now=NOW,
        )


def test_cli_generates_and_verifies_canonical_record(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    generated = tmp_path / "generated.json"
    verified = tmp_path / "verified.json"
    source.write_text(json.dumps(_base_record()), encoding="utf-8")

    assert (
        registry_module.main(
            ["generate", "--input", str(source), "--output", str(generated)]
        )
        == 0
    )
    assert verify_record(generated.read_bytes(), now=NOW)
    assert (
        registry_module.main(
            [
                "verify",
                "--input",
                str(generated),
                "--output",
                str(verified),
                "--now",
                "2026-08-30T01:00:00Z",
            ]
        )
        == 0
    )
    assert verified.read_bytes() == generated.read_bytes()


def test_cli_generates_executable_successor_from_signed_chain_head(
    tmp_path: Path,
) -> None:
    previous = build_record(_base_record())
    successor_source = json.loads(json.dumps(_base_record()))
    successor_head = "c" * 40
    successor_source["generation"] = 999
    successor_source["previous"] = {"invalid": "placeholder"}
    successor_source["head_oid"] = successor_head
    for action_name in ("version_action", "publish_action", "registry_action"):
        successor_source[action_name]["sha"] = successor_head
    successor_source["publisher"]["image_source_commit"] = successor_head
    successor_source["advertisement"].update(
        {
            "tag": "v1.6.0-beta.2",
            "target_sha": successor_head,
            "release_url": (
                "https://github.com/n24q02m/better-semantic-release/"
                "releases/tag/v1.6.0-beta.2"
            ),
        }
    )
    successor_source["workflow"]["release_commit"] = successor_head

    source_path = tmp_path / "successor-source.json"
    previous_path = tmp_path / "previous-registry.json"
    generated_path = tmp_path / "successor-registry.json"
    source_path.write_text(json.dumps(successor_source), encoding="utf-8")
    previous_path.write_bytes(canonical_record_bytes(previous))

    assert (
        registry_module.main(
            [
                "generate",
                "--input",
                str(source_path),
                "--previous",
                str(previous_path),
                "--output",
                str(generated_path),
            ]
        )
        == 0
    )
    successor = verify_record(generated_path.read_bytes(), now=NOW)
    assert successor["generation"] == 2
    assert successor["previous"] == {
        "generation": 1,
        "head_oid": previous["head_oid"],
        "record_hash": previous["record_hash"],
        "result_oid": previous["result_oid"],
        "result_hash": previous["result_hash"],
        "advertisement_tag": previous["advertisement"]["tag"],
    }
    assert select_latest([previous, successor], now=NOW) == successor


def test_remote_cli_renews_expired_predecessor_but_rejects_expired_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _base_record()
    source["issued_at"] = "2020-01-01T00:00:00Z"
    source["expires_at"] = "2020-02-01T00:00:00Z"
    raw = canonical_record_bytes(build_record(source))
    provider = _Provider(raw)
    expected_path = tmp_path / "expired-record.json"
    head_path = tmp_path / "expired-head.json"
    expected_path.write_bytes(raw)
    monkeypatch.setattr(
        registry_module,
        "HttpRegistryProvider",
        lambda **_kwargs: provider,
    )
    monkeypatch.setattr(
        registry_module,
        "verify_with_cosign",
        lambda *_args, **_kwargs: None,
    )

    assert (
        registry_module.main(
            [
                "head",
                "--repository",
                REPOSITORY,
                "--identity",
                (
                    f"https://github.com/{REPOSITORY}/.github/workflows/"
                    "cd.yml@refs/heads/main"
                ),
                "--output",
                str(head_path),
            ]
        )
        == 0
    )
    assert head_path.read_bytes() == raw
    assert (
        registry_module.main(
            [
                "load",
                "--expected",
                str(expected_path),
                "--repository",
                REPOSITORY,
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "registry error\n"


def test_cli_redacts_registry_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid = tmp_path / "secret-name.json"
    invalid.write_text('{"token":"must-not-leak"}', encoding="utf-8")

    assert registry_module.main(["verify", "--input", str(invalid)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "registry error\n"


class _AttestationHTTPProvider(registry_module.HttpRegistryProvider):
    def __init__(
        self,
        attestations: list[dict[str, Any]],
        attestation_verifier: registry_module.AttestationVerifier | None = None,
    ) -> None:
        super().__init__(
            timeout=1,
            attestation_verifier=(
                (lambda *_args: True)
                if attestation_verifier is None
                else attestation_verifier
            ),
        )
        self.attestations = attestations
        self.query_url = ""

    def _json(self, url: str) -> Any:
        self.query_url = url
        return {"attestations": self.attestations}


class _FakeHTTPResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


class _CapturingOpener:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.content_type = content_type
        self.requests: list[Any] = []

    def open(self, request: Any, *, timeout: float) -> _FakeHTTPResponse:
        assert timeout == 1
        self.requests.append(request)
        return _FakeHTTPResponse(self.body, self.content_type)


def _provenance_bundle(subject_digest: str = IMAGE_DIGEST) -> dict[str, Any]:
    payload = json.dumps(
        {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {
                    "name": "ghcr.io/n24q02m/better-semantic-release-publisher",
                    "digest": {"sha256": subject_digest[len("sha256:") :]},
                }
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "verificationMaterial": {
            "tlogEntries": [
                {
                    "inclusionProof": {
                        "checkpoint": {
                            "envelope": "rekor.sigstore.dev\n1\nfixture-root-hash\n"
                        }
                    }
                }
            ]
        },
        "dsseEnvelope": {
            "payload": base64.b64encode(payload).decode(),
            "payloadType": "application/vnd.in-toto+json",
            "signatures": [{"sig": "AA=="}],
        },
    }


def _snappy_literal(data: bytes) -> bytes:
    encoded = bytearray()
    size = len(data)
    remaining = size
    while remaining >= 0x80:
        encoded.append((remaining & 0x7F) | 0x80)
        remaining >>= 7
    encoded.append(remaining)
    literal_length = size - 1
    if literal_length < 60:
        encoded.append(literal_length << 2)
    else:
        length_bytes = literal_length.to_bytes(
            max(1, (literal_length.bit_length() + 7) // 8),
            "little",
        )
        encoded.append((59 + len(length_bytes)) << 2)
        encoded.extend(length_bytes)
    encoded.extend(data)
    return bytes(encoded)


@pytest.mark.parametrize(
    "compressed,expected",
    [
        (bytes([8, 12]) + b"abcd" + bytes([1, 4]), b"abcdabcd"),
        (bytes([12, 8]) + b"abc" + bytes([0x22, 3, 0]), b"abcabcabcabc"),
        (bytes([8, 12]) + b"abcd" + bytes([0x0F, 4, 0, 0, 0]), b"abcdabcd"),
    ],
)
def test_snappy_decoder_handles_all_copy_encodings(
    compressed: bytes, expected: bytes
) -> None:
    assert registry_module._decode_snappy_block(compressed) == expected


@pytest.mark.parametrize("compressed", [b"\x08\x01\x00", b"\x01\x04ab"])
def test_snappy_decoder_rejects_invalid_bounds(compressed: bytes) -> None:
    with pytest.raises(RegistryError, match="compression"):
        registry_module._decode_snappy_block(compressed)


def test_http_attestation_reader_uses_subject_digest_and_exact_bundle_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _provenance_bundle()
    bundle_url = (
        "https://tmaproduction.blob.core.windows.net/attestations/"
        "123/2026/08/30/937309.json.sn?sv=fixture"
    )
    raw_bundle = json.dumps(
        bundle,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    opener = _CapturingOpener(
        _snappy_literal(raw_bundle),
        content_type="application/x-snappy",
    )
    handlers: list[Any] = []
    monkeypatch.setattr(
        registry_module.urllib.request,
        "build_opener",
        lambda *values: handlers.extend(values) or opener,
    )
    monkeypatch.setattr(
        registry_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("explicit opener required"),
    )
    verified_bundles: list[tuple[bytes, str, str]] = []
    provider = _AttestationHTTPProvider(
        [
            {
                "repository_id": 123,
                "bundle_url": bundle_url,
                "initiator": "user",
                "bundle": None,
            }
        ],
        attestation_verifier=lambda raw, repository, digest: verified_bundles.append(
            (raw, repository, digest)
        ),
    )

    digest = provider.read_attestation(REPOSITORY, IMAGE_DIGEST, "937309")

    assert provider.query_url == (
        "https://api.github.com/repos/n24q02m/better-semantic-release/"
        "attestations/sha256%3A" + "3" * 64
    )
    assert len(opener.requests) == 1
    assert opener.requests[0].full_url == bundle_url
    assert "Authorization" not in opener.requests[0].headers
    assert "Authorization" not in opener.requests[0].unredirected_hdrs
    redirect_handlers = [
        item
        for item in handlers
        if isinstance(item, registry_module.urllib.request.HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    with pytest.raises(RegistryError, match="redirect"):
        redirect_handlers[0].redirect_request(
            opener.requests[0],
            None,
            302,
            "Found",
            Message(),
            bundle_url,
        )
    statement = json.loads(
        base64.b64decode(bundle["dsseEnvelope"]["payload"], validate=True)
    )
    expected = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    assert digest == hashlib.sha256(expected).hexdigest()
    assert verified_bundles == [(raw_bundle, REPOSITORY, IMAGE_DIGEST)]


@pytest.mark.parametrize("verifier_result", [False, 0, "unexpected"])
def test_http_attestation_reader_rejects_forged_bundle_after_crypto_verification(
    monkeypatch: pytest.MonkeyPatch,
    verifier_result: Any,
) -> None:
    bundle = _provenance_bundle()
    bundle_url = (
        "https://tmaproduction.blob.core.windows.net/attestations/"
        "123/2026/08/30/937309.json.sn?sv=fixture"
    )
    opener = _CapturingOpener(json.dumps(bundle).encode())
    monkeypatch.setattr(
        registry_module.urllib.request,
        "build_opener",
        lambda *_values: opener,
    )
    provider = _AttestationHTTPProvider(
        [
            {
                "repository_id": 123,
                "bundle_url": bundle_url,
                "initiator": "user",
                "bundle": None,
            }
        ],
        attestation_verifier=lambda *_args: verifier_result,
    )

    with pytest.raises(RegistryError, match="attestation signature"):
        provider.read_attestation(REPOSITORY, IMAGE_DIGEST, "937309")


def test_gh_attestation_verifier_enforces_identity_and_source_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_bundle = json.dumps(_provenance_bundle()).encode()
    captured: dict[str, Any] = {}

    def fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        timeout: float,
    ) -> Any:
        captured["command"] = command
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["timeout"] = timeout
        bundle_path = command[command.index("--bundle") + 1]
        with open(bundle_path, "rb") as stream:
            captured["bundle"] = stream.read()
        return registry_module.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(registry_module.subprocess, "run", fake_run)

    registry_module.verify_attestation_with_gh(
        raw_bundle,
        REPOSITORY,
        IMAGE_DIGEST,
        gh_binary="gh-fixture",
        timeout=7,
    )

    command = captured["command"]
    assert command[:4] == [
        "gh-fixture",
        "attestation",
        "verify",
        f"oci://ghcr.io/{REPOSITORY}-publisher@{IMAGE_DIGEST}",
    ]
    assert command[command.index("--repo") + 1] == REPOSITORY
    assert command[command.index("--cert-identity") + 1] == (
        f"https://github.com/{REPOSITORY}/.github/workflows/cd.yml@refs/heads/main"
    )
    assert command[command.index("--cert-oidc-issuer") + 1] == (
        "https://token.actions.githubusercontent.com"
    )
    assert command[command.index("--predicate-type") + 1] == (
        "https://slsa.dev/provenance/v1"
    )
    assert command[command.index("--source-ref") + 1] == "refs/heads/main"
    assert "--deny-self-hosted-runners" in command
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["timeout"] == 7
    assert captured["bundle"] == raw_bundle


def test_gh_attestation_verifier_rejects_failed_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        registry_module.subprocess,
        "run",
        lambda *_args, **_kwargs: registry_module.subprocess.CompletedProcess([], 1),
    )

    with pytest.raises(RegistryError, match="attestation signature"):
        registry_module.verify_attestation_with_gh(
            json.dumps(_provenance_bundle()).encode(),
            REPOSITORY,
            IMAGE_DIGEST,
        )


@pytest.mark.parametrize(
    "invalid_bundle",
    [
        {"mediaType": "application/test"},
        _provenance_bundle("sha256:" + "4" * 64),
    ],
)
def test_http_attestation_reader_rejects_non_provenance_bundle(
    monkeypatch: pytest.MonkeyPatch,
    invalid_bundle: dict[str, Any],
) -> None:
    bundle_url = (
        "https://tmaproduction.blob.core.windows.net/attestations/"
        "123/2026/08/30/937309.json.sn?sv=fixture"
    )
    opener = _CapturingOpener(json.dumps(invalid_bundle).encode())
    monkeypatch.setattr(
        registry_module.urllib.request,
        "build_opener",
        lambda *_values: opener,
    )
    provider = _AttestationHTTPProvider(
        [
            {
                "repository_id": 123,
                "bundle_url": bundle_url,
                "initiator": "user",
                "bundle": None,
            }
        ]
    )

    with pytest.raises(RegistryError, match="attestation"):
        provider.read_attestation(REPOSITORY, IMAGE_DIGEST, "937309")


def test_http_attestation_reader_rejects_zero_or_competing_matches() -> None:
    matching = {
        "repository_id": 123,
        "bundle_url": (
            "https://tmaproduction.blob.core.windows.net/attestations/"
            "123/2026/08/30/937309.json.sn?sv=fixture"
        ),
        "initiator": "user",
        "bundle": None,
    }
    for rows in ([], [matching, dict(matching)]):
        provider = _AttestationHTTPProvider(rows)
        with pytest.raises(RegistryError, match="attestation"):
            provider.read_attestation(REPOSITORY, IMAGE_DIGEST, "937309")


def test_http_attestation_reader_rejects_non_lf_checkpoint_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _provenance_bundle()
    bundle["verificationMaterial"]["tlogEntries"][0]["inclusionProof"]["checkpoint"][
        "envelope"
    ] = "rekor.sigstore.dev\r\n1\nfixture-root-hash\n"
    bundle_url = (
        "https://tmaproduction.blob.core.windows.net/attestations/"
        "123/2026/08/30/937309.json.sn?sv=fixture"
    )
    opener = _CapturingOpener(json.dumps(bundle).encode())
    monkeypatch.setattr(
        registry_module.urllib.request,
        "build_opener",
        lambda *_values: opener,
    )
    provider = _AttestationHTTPProvider(
        [
            {
                "repository_id": 123,
                "bundle_url": bundle_url,
                "initiator": "user",
                "bundle": None,
            }
        ],
        attestation_verifier=lambda *_args: pytest.fail(
            "invalid checkpoint reached verifier"
        ),
    )

    with pytest.raises(RegistryError, match="control character"):
        provider.read_attestation(REPOSITORY, IMAGE_DIGEST, "937309")


def test_http_provider_exchanges_scoped_ghcr_token_before_manifest_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = registry_module.HttpRegistryProvider(
        token="github-token",
        timeout=1,
    )
    requests: list[tuple[str, str, str | None, bool]] = []

    def fake_request(
        url: str,
        *,
        accept: str = "application/json",
        bearer_token: str | None = None,
        include_github_token: bool = True,
    ) -> tuple[bytes, Message]:
        requests.append((url, accept, bearer_token, include_github_token))
        headers = Message()
        if url.startswith("https://ghcr.io/token?"):
            return b'{"token":"ghcr-pull-token"}', headers
        headers["docker-content-digest"] = IMAGE_DIGEST
        return b"{}", headers

    monkeypatch.setattr(provider, "_request", fake_request)

    assert (
        provider.read_oci_manifest(
            "ghcr.io/n24q02m/better-semantic-release-publisher",
            IMAGE_DIGEST,
        )
        == IMAGE_DIGEST
    )
    token_url = (
        "https://ghcr.io/token?service=ghcr.io&"
        "scope=repository%3An24q02m%2Fbetter-semantic-release-publisher%3Apull"
    )
    manifest_url = (
        "https://ghcr.io/v2/n24q02m/better-semantic-release-publisher/"
        f"manifests/{IMAGE_DIGEST}"
    )
    manifest_accept = (
        "application/vnd.oci.image.manifest.v1+json, "
        "application/vnd.docker.distribution.manifest.v2+json"
    )
    assert requests == [
        (
            token_url,
            "application/json",
            None,
            False,
        ),
        (
            manifest_url,
            manifest_accept,
            "ghcr-pull-token",
            False,
        ),
    ]


@pytest.mark.parametrize(
    "image_ref",
    [
        "ghcr.io/owner/ghcr.io/repository",
        "owner/ghcr.io/repository",
        "https://ghcr.io/owner/repository",
    ],
)
def test_http_provider_rejects_embedded_or_non_authority_ghcr_reference(
    monkeypatch: pytest.MonkeyPatch, image_ref: str
) -> None:
    provider = registry_module.HttpRegistryProvider(timeout=1)
    monkeypatch.setattr(
        provider,
        "_request",
        lambda *_args, **_kwargs: pytest.fail("invalid image reference reached HTTP"),
    )

    with pytest.raises(RegistryError, match="provider image ref"):
        provider.read_oci_manifest(image_ref, IMAGE_DIGEST)


def test_http_provider_omits_github_token_for_anonymous_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _CapturingOpener(b'{"token":"ghcr-pull-token"}')
    monkeypatch.setattr(
        registry_module.urllib.request,
        "build_opener",
        lambda *_values: opener,
    )
    provider = registry_module.HttpRegistryProvider(
        token="github-token",
        timeout=1,
    )

    provider._json(
        "https://ghcr.io/token?service=ghcr.io&scope=repository%3Afixture%3Apull",
        include_github_token=False,
    )

    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert "Authorization" not in request.headers
    assert "Authorization" not in request.unredirected_hdrs


def test_http_provider_never_forwards_token_and_rejects_foreign_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _CapturingOpener(b"{}")
    handlers: list[Any] = []
    monkeypatch.setattr(
        registry_module.urllib.request,
        "build_opener",
        lambda *values: handlers.extend(values) or opener,
    )
    monkeypatch.setattr(
        registry_module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("explicit opener required"),
    )
    provider = registry_module.HttpRegistryProvider(token="github-token", timeout=1)

    provider._request("https://api.github.com/repos/n24q02m/example")

    assert len(opener.requests) == 1
    original = opener.requests[0]
    assert "Authorization" not in original.headers
    assert original.unredirected_hdrs["Authorization"] == "Bearer github-token"
    redirect_handlers = [
        item
        for item in handlers
        if isinstance(item, registry_module.urllib.request.HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    allowed = redirect_handlers[0].redirect_request(
        original,
        None,
        302,
        "Found",
        Message(),
        "https://release-assets.githubusercontent.com/path/asset",
    )
    assert allowed is not None
    assert "Authorization" not in allowed.headers
    assert "Authorization" not in allowed.unredirected_hdrs
    with pytest.raises(RegistryError, match="redirect"):
        redirect_handlers[0].redirect_request(
            original,
            None,
            302,
            "Found",
            Message(),
            "https://attacker.example/path",
        )


API_BASE = f"https://api.github.com/repos/{REPOSITORY}"
RELEASES_URL = f"{API_BASE}/releases?per_page=100&page=1"
ASSETS_URL = f"{API_BASE}/releases/77/assets?per_page=100&page=1"
BLOB_URL = f"{API_BASE}/contents/action.yml?ref={HEAD}"
TAG_REF_URL = f"{API_BASE}/git/ref/tags/v1.6.0-beta.1"
GHCR_TOKEN_URL = (
    "https://ghcr.io/token?service=ghcr.io&"
    "scope=repository%3An24q02m%2Fbetter-semantic-release-publisher%3Apull"
)
MANIFEST_URL = (
    "https://ghcr.io/v2/n24q02m/better-semantic-release-publisher/"
    f"manifests/{IMAGE_DIGEST}"
)
ATTESTATIONS_URL = f"{API_BASE}/attestations/sha256%3A" + "3" * 64
BUNDLE_URL = (
    "https://tmaproduction.blob.core.windows.net/attestations/"
    "123/2026/08/30/937309.json.sn?sv=fixture"
)
IDENTITY = f"https://github.com/{REPOSITORY}/.github/workflows/cd.yml@refs/heads/main"
PINNED_IMAGE = (
    f"docker://ghcr.io/n24q02m/better-semantic-release-publisher@{IMAGE_DIGEST}"
)
OTHER_REPOSITORY = "n24q02m/other-repo"
ASSET_LIMIT = 16 * 1024 * 1024
HTTP_LIMIT = 32 * 1024 * 1024


def _exact(message: str) -> str:
    return f"^{re.escape(message)}$"


def _set_path(document: dict[str, Any], dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    target = document
    for key in parents:
        target = target[key]
    target[leaf] = copy.deepcopy(value)


def _release_url(tag: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/tag/{tag}"


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "generation": record["generation"],
        "head_oid": record["head_oid"],
        "record_hash": record["record_hash"],
        "result_oid": record["result_oid"],
        "result_hash": record["result_hash"],
        "advertisement_tag": record["advertisement"]["tag"],
    }


def _valid_previous() -> dict[str, Any]:
    return {
        "generation": 1,
        "head_oid": HEAD,
        "record_hash": "0" * 64,
        "result_oid": HEAD,
        "result_hash": "0" * 64,
        "advertisement_tag": "v1.5.0",
    }


def _tagged_source(
    tag: str, *, phase: str = "G1", channel: str = "beta"
) -> dict[str, Any]:
    source = _base_record()
    source["phase"] = phase
    source["channel"] = channel
    source["advertisement"]["tag"] = tag
    source["advertisement"]["release_url"] = _release_url(tag)
    source["workflow"]["environment"] = (
        "stable-publish" if phase == "G2" else "beta-publish"
    )
    return source


def _successor_source(previous: dict[str, Any], tag: str) -> dict[str, Any]:
    source = _tagged_source(tag)
    source["generation"] = previous["generation"] + 1
    source["previous"] = _summary(previous)
    source["issued_at"] = "2026-08-30T00:30:00Z"
    source["expires_at"] = "2026-09-07T00:00:00Z"
    return source


def _load_head(provider: Any, **overrides: Any) -> dict[str, Any] | None:
    arguments: dict[str, Any] = {
        "provider": provider,
        "signature_verifier": lambda _payload, _bundle: None,
        "repository": REPOSITORY,
        "now": NOW,
    }
    arguments.update(overrides)
    return load_remote_chain_head(**arguments)


def _load_registry(provider: Any, expected: Any, **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "expected": expected,
        "provider": provider,
        "signature_verifier": lambda _payload, _bundle: None,
        "repository": REPOSITORY,
        "now": NOW,
    }
    arguments.update(overrides)
    return load_remote_registry(**arguments)


_INVALID_FIELDS = [
    pytest.param(
        {"schema_version": "bsr-action-registry/v2"},
        "record schema version",
        id="schema-version",
    ),
    pytest.param({"generation": 0}, "record generation", id="generation-zero"),
    pytest.param({"generation": True}, "record generation", id="generation-bool"),
    pytest.param({"channel": "stable"}, "record phase channel", id="phase-channel"),
    pytest.param({"issued_at": "2026-08-30"}, "record issued at", id="issued-format"),
    pytest.param(
        {"issued_at": "2026-02-30T00:00:00Z"},
        "record issued at",
        id="issued-calendar",
    ),
    pytest.param(
        {"expires_at": "2026-08-30T00:00:00Z"},
        "record expiry",
        id="expires-not-after-issue",
    ),
    pytest.param({"owner": ""}, "record owner", id="owner-empty"),
    pytest.param(
        {"owner": "release\x07security"},
        "record control character",
        id="owner-control",
    ),
    pytest.param(
        {"generation": 3, "previous": _valid_previous()},
        "record previous generation",
        id="previous-gap",
    ),
    pytest.param(
        {"generation": 2, "previous": "v1.5.0"},
        "record previous object",
        id="previous-scalar",
    ),
    pytest.param(
        {"generation": 2, "previous": {**_valid_previous(), "signed": True}},
        "record previous unknown key",
        id="previous-extra-key",
    ),
    pytest.param(
        {
            "generation": 2,
            "previous": {
                key: value
                for key, value in _valid_previous().items()
                if key != "advertisement_tag"
            },
        },
        "record previous missing key",
        id="previous-missing-key",
    ),
    pytest.param({"head_oid": "g" * 40}, "record head oid", id="head-oid"),
    pytest.param({"head_hash": "B" * 64}, "record head hash", id="head-hash"),
    pytest.param(
        {"version_action.repository": "not-a-repository"},
        "record repository",
        id="action-repository-format",
    ),
    pytest.param(
        {"publish_action.repository": OTHER_REPOSITORY},
        "record publish action repository",
        id="publish-action-repository",
    ),
    pytest.param(
        {"publish_action.path": "action.yml"},
        "record publish action path",
        id="publish-action-path",
    ),
    pytest.param(
        {"publish_action.sha": "c" * 40},
        "record publish action sha",
        id="publish-action-sha",
    ),
    pytest.param(
        {
            "publisher.identity": (
                f"https://github.com/{REPOSITORY}/.github/workflows/"
                "ci.yml@refs/heads/main"
            )
        },
        "record publisher identity",
        id="publisher-identity",
    ),
    pytest.param(
        {"publisher.image_digest": "sha256:" + "G" * 64},
        "record image digest",
        id="publisher-image-digest",
    ),
    pytest.param(
        {"publisher.image_source_commit": "c" * 40},
        "record publisher source commit",
        id="publisher-source-commit",
    ),
    pytest.param(
        {"publisher.provenance_repository": OTHER_REPOSITORY},
        "record provenance repository",
        id="provenance-repository",
    ),
    pytest.param(
        {"publisher.provenance_workflow": ".github/workflows/ci.yml"},
        "record provenance workflow",
        id="provenance-workflow",
    ),
    pytest.param(
        {"publisher.provenance_attestation_id": "93a309"},
        "record provenance attestation id",
        id="attestation-id-not-digits",
    ),
    pytest.param(
        {"publisher.provenance_attestation_id": "0"},
        "record provenance attestation id",
        id="attestation-id-zero",
    ),
    pytest.param(
        {"advertisement.repository": OTHER_REPOSITORY},
        "record advertisement repository",
        id="advertisement-repository",
    ),
    pytest.param(
        {"advertisement.tag": "1.6.0"}, "record tag", id="advertisement-tag-format"
    ),
    pytest.param(
        {"advertisement.tag": "v1.6.0"},
        "record phase tag",
        id="advertisement-stable-tag-on-beta",
    ),
    pytest.param(
        {"advertisement.target_sha": "c" * 40},
        "record advertisement target",
        id="advertisement-target",
    ),
    pytest.param(
        {"advertisement.release_url": _release_url("v9.9.9")},
        "record release url",
        id="advertisement-release-url",
    ),
    pytest.param(
        {"advertisement.registry_asset": "registry.json"},
        "record registry asset",
        id="advertisement-registry-asset",
    ),
    pytest.param(
        {"advertisement.signature_asset": "registry.sigstore.json"},
        "record signature asset",
        id="advertisement-signature-asset",
    ),
    pytest.param(
        {
            "advertisement.image_ref": (
                "ghcr.io/n24q02m/better-semantic-release-publisher:latest"
            )
        },
        "record advertisement image ref",
        id="advertisement-image-ref",
    ),
    pytest.param(
        {"workflow.repository": OTHER_REPOSITORY},
        "record workflow repository",
        id="workflow-repository",
    ),
    pytest.param(
        {"workflow.path": ".github/workflows/ci.yml"},
        "record workflow path",
        id="workflow-path",
    ),
    pytest.param(
        {"workflow.release_commit": "c" * 40},
        "record workflow release commit",
        id="workflow-release-commit",
    ),
    pytest.param(
        {"workflow.environment": "stable-publish"},
        "record workflow environment",
        id="workflow-environment",
    ),
]


@pytest.mark.parametrize("updates,message", _INVALID_FIELDS)
def test_build_record_rejects_each_invalid_field(
    updates: dict[str, Any], message: str
) -> None:
    source = _base_record()
    for dotted, value in updates.items():
        _set_path(source, dotted, value)

    with pytest.raises(RegistryError, match=_exact(message)):
        build_record(source)


@pytest.mark.parametrize(
    "value,message",
    [
        pytest.param([], "record object", id="array"),
        pytest.param(
            {**_base_record(), "signature": "x"}, "record unknown key", id="extra-key"
        ),
        pytest.param(
            {key: value for key, value in _base_record().items() if key != "owner"},
            "record missing key",
            id="missing-key",
        ),
        pytest.param({**_base_record(), 1: "x"}, "record key", id="non-string-key"),
    ],
)
def test_build_record_rejects_non_object_or_drifted_key_set(
    value: Any, message: str
) -> None:
    with pytest.raises(RegistryError, match=_exact(message)):
        build_record(value)


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("result_oid", "c" * 40, "record result oid"),
        ("record_hash", "0" * 64, "record hash"),
        ("result_hash", "0" * 64, "result hash"),
    ],
)
def test_canonical_bytes_reject_tampered_derived_fields(
    field: str, value: str, message: str
) -> None:
    record = build_record(_base_record())
    record[field] = value

    with pytest.raises(RegistryError, match=_exact(message)):
        canonical_record_bytes(record)


@pytest.mark.parametrize(
    "raw,message",
    [
        pytest.param(b" " * (2 * 1024 * 1024 + 1), "record size", id="oversized"),
        pytest.param(b"\xff\n", "record encoding", id="not-utf8"),
        pytest.param(b'{"owner":NaN}\n', "record json", id="nan-constant"),
        pytest.param(b'{"owner":\n', "record json", id="truncated"),
        pytest.param(b"[]\n", "record object", id="array"),
        pytest.param(
            b'{"owner":"\\u0001"}\n', "record control character", id="escaped-control"
        ),
        pytest.param(
            b'{"owner":"\\ud800"}\n', "record canonical json", id="lone-surrogate"
        ),
    ],
)
def test_verify_record_rejects_malformed_documents(raw: bytes, message: str) -> None:
    with pytest.raises(RegistryError, match=_exact(message)):
        verify_record(raw, now=NOW)


@pytest.mark.parametrize(
    "now,message",
    [
        pytest.param(datetime(2026, 8, 30, 1, 0), "record time", id="naive"),
        pytest.param(
            datetime(2026, 8, 29, 23, 59, tzinfo=timezone.utc),
            "record not issued",
            id="before-issue",
        ),
    ],
)
def test_verify_record_rejects_naive_or_premature_time(
    now: datetime, message: str
) -> None:
    raw = canonical_record_bytes(build_record(_base_record()))

    with pytest.raises(RegistryError, match=_exact(message)):
        verify_record(raw, now=now)


def test_build_chained_record_without_previous_resets_to_root() -> None:
    source = _base_record()
    source["generation"] = 7
    source["previous"] = {"stale": "summary"}

    record = registry_module.build_chained_record(source, None)

    assert record == build_record(_base_record())
    assert record["generation"] == 1
    assert record["previous"] is None


def test_build_chained_record_links_successor_to_signed_bytes() -> None:
    first = build_record(_base_record())

    successor = registry_module.build_chained_record(
        _tagged_source("v1.6.0-beta.2"), canonical_record_bytes(first), now=NOW
    )

    assert successor["generation"] == 2
    assert successor["previous"] == _summary(first)
    assert select_latest([successor, first], now=NOW) == successor


def test_build_chained_record_rejects_stable_to_beta_regression() -> None:
    stable = build_record(_tagged_source("v1.6.0", phase="G2", channel="stable"))

    with pytest.raises(RegistryError, match=_exact("phase chain")):
        registry_module.build_chained_record(
            _tagged_source("v1.7.0-beta.1"), canonical_record_bytes(stable), now=NOW
        )


def test_select_latest_rejects_empty_and_unbounded_collections() -> None:
    raw = canonical_record_bytes(build_record(_base_record()))

    with pytest.raises(RegistryError, match=_exact("record collection")):
        select_latest([], now=NOW)
    with pytest.raises(RegistryError, match=_exact("record collection")):
        select_latest(itertools.repeat(raw, 256 * 100 + 1), now=NOW)


def test_select_latest_rejects_records_from_forked_repository() -> None:
    first = build_record(_base_record())
    fork = build_record(
        json.loads(json.dumps(_base_record()).replace(REPOSITORY, OTHER_REPOSITORY))
    )
    assert fork["advertisement"]["repository"] == OTHER_REPOSITORY

    with pytest.raises(RegistryError, match=_exact("record repository fork")):
        select_latest([first, fork], now=NOW)


def test_select_latest_rejects_duplicate_generation() -> None:
    first = build_record(_base_record())

    with pytest.raises(RegistryError, match=_exact("duplicate generation")):
        select_latest([first, copy.deepcopy(first)], now=NOW)


def test_select_latest_rejects_chain_without_root_or_with_gap() -> None:
    first = build_record(_base_record())
    second = build_record(_successor_source(first, "v1.6.0-beta.2"))
    third = build_record(_successor_source(second, "v1.6.0-beta.3"))

    assert select_latest([third, first, second], now=NOW) == third
    for records in ([second, third], [first, third]):
        with pytest.raises(
            RegistryError, match=_exact("previous chain missing predecessor")
        ):
            select_latest(records, now=NOW)


def test_select_latest_rejects_stable_to_beta_regression() -> None:
    stable = build_record(_tagged_source("v1.6.0", phase="G2", channel="stable"))
    beta = build_record(_successor_source(stable, "v1.7.0-beta.1"))

    with pytest.raises(RegistryError, match=_exact("phase chain")):
        select_latest([stable, beta], now=NOW)


class _ChainProvider(_Provider):
    def __init__(self, *records: dict[str, Any]) -> None:
        super().__init__(canonical_record_bytes(records[-1]))
        self.raw_by_release = {
            index: canonical_record_bytes(record)
            for index, record in enumerate(records, start=1)
        }
        self.tag_by_release = {
            index: record["advertisement"]["tag"]
            for index, record in enumerate(records, start=1)
        }
        self.downloads: list[str] = []

    def list_releases(
        self, repository: str, *, page: int, per_page: int
    ) -> list[dict[str, Any]]:
        assert repository == REPOSITORY
        assert per_page == 100
        self.release_pages.append(page)
        if page != 1:
            return []
        return [
            {"id": release_id, "tag_name": tag, "html_url": _release_url(tag)}
            for release_id, tag in self.tag_by_release.items()
        ]

    def list_release_assets(
        self, repository: str, release_id: int, *, page: int, per_page: int
    ) -> list[dict[str, Any]]:
        assert repository == REPOSITORY
        assert per_page == 100
        if page != 1:
            return []
        return [
            {
                "name": "bsr-action-registry.json",
                "url": f"asset://record/{release_id}",
            },
            {
                "name": "bsr-action-registry.sigstore.json",
                "browser_download_url": f"asset://bundle/{release_id}",
            },
        ]

    def download_asset(self, url: str) -> bytes:
        self.downloads.append(url)
        kind, release_id = url[len("asset://") :].split("/")
        return self.raw_by_release[int(release_id)] if kind == "record" else b"bundle"

    def peel_tag(self, repository: str, tag: str) -> str:
        assert repository == REPOSITORY
        assert tag in self.tag_by_release.values()
        return HEAD


def test_remote_load_rejects_provider_without_read_methods() -> None:
    with pytest.raises(RegistryError, match=_exact("provider method")):
        _load_head(object())


def test_remote_load_keeps_registry_errors_and_redacts_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))

    def rate_limited(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RegistryError("provider rate limited")

    def crashed(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise OSError("connection reset while sending test-token")

    monkeypatch.setattr(provider, "list_releases", rate_limited)
    with pytest.raises(RegistryError, match=_exact("provider rate limited")):
        _load_head(provider)

    monkeypatch.setattr(provider, "list_releases", crashed)
    with pytest.raises(RegistryError, match=_exact("provider read")) as caught:
        _load_head(provider)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "page_value,message",
    [
        pytest.param({"id": 77}, "provider pagination", id="object-page"),
        pytest.param(
            [{"id": index} for index in range(101)],
            "provider pagination",
            id="oversized-page",
        ),
        pytest.param(["v1.6.0-beta.1"], "provider response", id="scalar-release"),
    ],
)
def test_remote_load_rejects_malformed_release_pages(
    monkeypatch: pytest.MonkeyPatch, page_value: Any, message: str
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))
    monkeypatch.setattr(provider, "list_releases", lambda *_args, **_kwargs: page_value)

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_head(provider)


def test_remote_load_stops_at_release_pagination_bound() -> None:
    pages: list[int] = []

    class _EndlessReleases:
        def list_releases(
            self, repository: str, *, page: int, per_page: int
        ) -> list[dict[str, Any]]:
            assert repository == REPOSITORY
            assert per_page == 100
            pages.append(page)
            return [{"tag_name": "v1.5.0"}]

    with pytest.raises(RegistryError, match=_exact("provider pagination bound")):
        _load_head(_EndlessReleases(), excluded_tag="v1.5.0")
    assert pages == list(range(1, 257))


@pytest.mark.parametrize("release_id", [0, -7, True, "77", None])
def test_remote_load_rejects_invalid_release_id(
    monkeypatch: pytest.MonkeyPatch, release_id: Any
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))
    release = {
        "id": release_id,
        "tag_name": "v1.6.0-beta.1",
        "html_url": _release_url("v1.6.0-beta.1"),
    }
    monkeypatch.setattr(provider, "list_releases", lambda *_args, **_kwargs: [release])

    with pytest.raises(RegistryError, match=_exact("release id")):
        _load_head(provider)


def test_remote_load_rejects_duplicate_release_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))
    release = {
        "id": 77,
        "tag_name": "v1.6.0-beta.1",
        "html_url": _release_url("v1.6.0-beta.1"),
    }
    monkeypatch.setattr(
        provider,
        "list_releases",
        lambda *_args, page, **_kwargs: [release, dict(release)] if page == 1 else [],
    )

    with pytest.raises(RegistryError, match=_exact("release duplicate")):
        _load_head(provider)


@pytest.mark.parametrize(
    "assets,message",
    [
        pytest.param(
            [
                {"name": "bsr-action-registry.json", "url": "asset://record"},
                {"name": "bsr-action-registry.json", "url": "asset://copy"},
            ],
            "asset duplicate",
            id="duplicate-name",
        ),
        pytest.param(
            [{"name": "bsr-action-registry.json", "url": "asset://record"}],
            "asset pair",
            id="unsigned-record",
        ),
        pytest.param(
            [{"name": "bsr-action-registry.sigstore.json", "url": "asset://bundle"}],
            "asset pair",
            id="orphan-signature",
        ),
        pytest.param([{"url": "asset://record"}], "record asset name", id="unnamed"),
        pytest.param(
            [{"name": "bsr-action-registry.json"}], "record asset url", id="no-url"
        ),
    ],
)
def test_remote_load_rejects_inconsistent_release_assets(
    monkeypatch: pytest.MonkeyPatch, assets: list[dict[str, Any]], message: str
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))
    monkeypatch.setattr(
        provider,
        "list_release_assets",
        lambda *_args, page, **_kwargs: assets if page == 1 else [],
    )

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_head(provider)


def test_remote_load_without_registry_assets_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    monkeypatch.setattr(
        provider,
        "list_release_assets",
        lambda *_args, page, **_kwargs: (
            [{"name": "notes.txt", "url": "asset://notes"}] if page == 1 else []
        ),
    )

    assert _load_head(provider) is None
    with pytest.raises(RegistryError, match=_exact("registry absent")):
        _load_registry(provider, record)


def test_remote_load_bounds_downloaded_assets_and_blobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = build_record(_base_record())
    raw = canonical_record_bytes(record)
    oversized = b"x" * (ASSET_LIMIT + 1)
    provider = _Provider(raw)

    monkeypatch.setattr(provider, "download_asset", lambda _url: "registry-text")
    with pytest.raises(RegistryError, match=_exact("record registry asset")):
        _load_head(provider)

    monkeypatch.setattr(provider, "download_asset", lambda _url: oversized)
    with pytest.raises(RegistryError, match=_exact("record registry asset size")):
        _load_head(provider)

    provider = _Provider(raw)
    provider.blobs["action.yml"]["bytes"] = oversized
    with pytest.raises(RegistryError, match=_exact("record blob size")):
        _load_registry(provider, record)


def _verifier_with_registry_error(_payload: bytes, _bundle: bytes) -> None:
    raise RegistryError("signature bundle expired")


def _verifier_with_crash(_payload: bytes, _bundle: bytes) -> None:
    raise ValueError("cosign crashed while holding test-token")


@pytest.mark.parametrize(
    "verifier,message",
    [
        pytest.param(
            _verifier_with_registry_error,
            "signature bundle expired",
            id="registry-error",
        ),
        pytest.param(_verifier_with_crash, "signature verification", id="crash"),
        pytest.param(
            lambda _payload, _bundle: False, "signature verification", id="false"
        ),
    ],
)
def test_remote_load_enforces_signature_verifier_outcome(
    verifier: Any, message: str
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_head(provider, signature_verifier=verifier)


def test_remote_load_rejects_release_that_does_not_advertise_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))
    release = {
        "id": 77,
        "tag_name": "v1.6.0-beta.1",
        "html_url": _release_url("v1.6.0-beta.9"),
    }
    monkeypatch.setattr(
        provider,
        "list_releases",
        lambda *_args, page, **_kwargs: [release] if page == 1 else [],
    )
    verified: list[tuple[bytes, bytes]] = []

    with pytest.raises(RegistryError, match=_exact("advertisement")):
        _load_head(
            provider,
            signature_verifier=lambda payload, bundle: verified.append(
                (payload, bundle)
            ),
        )
    assert verified == []


@pytest.mark.parametrize(
    "overrides,message",
    [
        pytest.param(
            {"repository": "not a repository"},
            "record repository",
            id="repository-format",
        ),
        pytest.param(
            {"repository": "n24q02m/repo\x01"},
            "record repository",
            id="repository-control",
        ),
        pytest.param({"excluded_tag": "latest"}, "record tag", id="excluded-tag"),
        pytest.param(
            {"signature_verifier": "cosign"}, "signature verifier", id="verifier"
        ),
    ],
)
def test_chain_head_rejects_invalid_arguments_before_reading(
    overrides: dict[str, Any], message: str
) -> None:
    provider = _Provider(canonical_record_bytes(build_record(_base_record())))

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_head(provider, **overrides)
    assert provider.release_pages == []


@pytest.mark.parametrize(
    "overrides,message",
    [
        pytest.param(
            {"signature_verifier": "cosign"}, "signature verifier", id="verifier"
        ),
        pytest.param({"expected": []}, "expected record", id="expected-array"),
        pytest.param(
            {"repository": OTHER_REPOSITORY},
            "expected repository",
            id="other-repository",
        ),
    ],
)
def test_loader_rejects_invalid_arguments_before_reading(
    overrides: dict[str, Any], message: str
) -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))

    arguments = {"expected": record, **overrides}

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_registry(provider, **arguments)
    assert provider.release_pages == []


def test_loader_accepts_expected_record_as_canonical_bytes() -> None:
    raw = canonical_record_bytes(build_record(_base_record()))

    output = _load_registry(_Provider(raw), raw)

    assert output["loader_output_sha256"] == (
        "8baf118b99317de8acaa0cd629edbe3f7aa49a877e91e30e3112555ee877fce8"
    )


def test_loader_selects_chain_head_across_releases() -> None:
    first = build_record(_base_record())
    second = build_record(_successor_source(first, "v1.6.0-beta.2"))
    provider = _ChainProvider(first, second)

    output = _load_registry(provider, second)

    assert output["generation"] == 2
    assert output["advertisement_tag"] == "v1.6.0-beta.2"
    assert output["record_hash"] == second["record_hash"]
    assert provider.downloads == [
        "asset://record/1",
        "asset://bundle/1",
        "asset://record/2",
        "asset://bundle/2",
    ]


def test_loader_rejects_expected_record_that_is_not_chain_head() -> None:
    first = build_record(_base_record())
    second = build_record(_successor_source(first, "v1.6.0-beta.2"))

    with pytest.raises(RegistryError, match=_exact("expected tuple")):
        _load_registry(_ChainProvider(first, second), first)


def test_loader_rejects_phase_channel_drift_from_expected_record() -> None:
    stable = build_record(_tagged_source("v1.6.0", phase="G2", channel="stable"))

    with pytest.raises(RegistryError, match=_exact("phase channel")):
        _load_registry(_ChainProvider(stable), build_record(_base_record()))


@pytest.mark.parametrize(
    "method,value,message",
    [
        pytest.param("peel_tag", "c" * 40, "tag target", id="moved-tag"),
        pytest.param("peel_tag", None, "tag target", id="unpeeled-tag"),
        pytest.param(
            "read_oci_manifest", "sha256:" + "4" * 64, "oci manifest", id="manifest"
        ),
        pytest.param("read_attestation", "9" * 64, "attestation", id="attestation"),
    ],
)
def test_loader_rejects_fresh_readback_drift(
    monkeypatch: pytest.MonkeyPatch, method: str, value: Any, message: str
) -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    monkeypatch.setattr(provider, method, lambda *_args: value)

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_registry(provider, record)


@pytest.mark.parametrize(
    "path,blob,message",
    [
        pytest.param("action.yml", [], "blob response", id="array-blob"),
        pytest.param(
            "action.yml",
            {"oid": _blob_oid(10), "content": 42},
            "blob content",
            id="numeric-content",
        ),
        pytest.param(
            "action.yml",
            {"oid": _blob_oid(10), "content": "YQ==", "encoding": "utf-8"},
            "blob content",
            id="unsupported-encoding",
        ),
        pytest.param(
            "action.yml", {"oid": _blob_oid(10)}, "blob content", id="no-content"
        ),
        pytest.param(
            "action.yml",
            {"oid": _blob_oid(10), "bytes": "text"},
            "record blob",
            id="text-bytes",
        ),
        pytest.param(
            "action.yml",
            {"oid": _blob_oid(10), "bytes": b"tampered"},
            "blob sha256",
            id="tampered",
        ),
        pytest.param(
            "publish-action/Dockerfile",
            [],
            "publisher source",
            id="publisher-source-array",
        ),
    ],
)
def test_loader_rejects_malformed_or_tampered_blobs(
    path: str, blob: Any, message: str
) -> None:
    record = build_record(_base_record())
    provider = _Provider(canonical_record_bytes(record))
    provider.blobs[path] = blob

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_registry(provider, record)


def _publish_action(image_lines: str) -> bytes:
    return f"name: BSR publisher\nruns:\n  using: docker\n{image_lines}".encode()


def _record_with_publish_action(content: bytes) -> tuple[dict[str, Any], _Provider]:
    source = _base_record()
    source["publish_action"]["action_sha256"] = _sha256(content)
    record = build_record(source)
    provider = _Provider(canonical_record_bytes(record))
    provider.blobs["publish-action/action.yml"]["bytes"] = content
    return record, provider


@pytest.mark.parametrize(
    "content,message",
    [
        pytest.param(
            b"runs:\n  image: \xff\n", "publisher action encoding", id="not-utf8"
        ),
        pytest.param(_publish_action(""), "publisher action image", id="no-image"),
        pytest.param(
            _publish_action(f"  image: {PINNED_IMAGE}\n  image: {PINNED_IMAGE}\n"),
            "publisher action image",
            id="two-images",
        ),
        pytest.param(
            _publish_action(
                '  image: "docker://ghcr.io/n24q02m/'
                'better-semantic-release-publisher:latest"\n'
            ),
            "publisher action image",
            id="mutable-tag",
        ),
        pytest.param(
            _publish_action("  image: Dockerfile\n"),
            "publisher action image",
            id="local-dockerfile",
        ),
        pytest.param(
            _publish_action(
                "  image: docker://ghcr.io/n24q02m/"
                f"better-semantic-release-publisher@sha256:{'4' * 64}\n"
            ),
            "publisher action image",
            id="other-digest",
        ),
    ],
)
def test_loader_rejects_unpinned_or_foreign_publisher_image(
    content: bytes, message: str
) -> None:
    record, provider = _record_with_publish_action(content)

    with pytest.raises(RegistryError, match=_exact(message)):
        _load_registry(provider, record)


def test_loader_accepts_single_quoted_commented_publisher_image() -> None:
    record, provider = _record_with_publish_action(
        _publish_action(f"  image: '{PINNED_IMAGE}'  # pinned by digest\n")
    )

    output = _load_registry(provider, record)

    assert output["publisher_image_digest"] == IMAGE_DIGEST
    assert output["record_hash"] == record["record_hash"]


class _RoutingOpener:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[Any] = []
        self.timeouts: list[float] = []
        self.unrouted: list[str] = []

    def open(self, request: Any, *, timeout: float) -> _FakeHTTPResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        route = self.routes.get(request.full_url)
        if route is None:
            self.unrouted.append(request.full_url)
            raise urllib.error.URLError("unrouted fixture URL")
        if isinstance(route, BaseException):
            raise route
        body, headers = route
        response = _FakeHTTPResponse(body, headers["Content-Type"])
        response.headers.update(headers)
        return response


def _json_route(value: Any) -> tuple[bytes, dict[str, str]]:
    return json.dumps(value).encode(), {"Content-Type": "application/json"}


def _install_opener(monkeypatch: pytest.MonkeyPatch, opener: _RoutingOpener) -> None:
    monkeypatch.setattr(
        registry_module.urllib.request, "build_opener", lambda *_handlers: opener
    )


def _github_routes(record_raw: bytes, bundle_raw: bytes) -> dict[str, Any]:
    tag = "v1.6.0-beta.1"
    tag_object = "e" * 40
    octet_stream = {"Content-Type": "application/octet-stream"}
    routes: dict[str, Any] = {
        RELEASES_URL: _json_route(
            [{"id": 77, "tag_name": tag, "html_url": _release_url(tag)}]
        ),
        f"{API_BASE}/releases?per_page=100&page=2": _json_route([]),
        ASSETS_URL: _json_route(
            [
                {
                    "name": "bsr-action-registry.json",
                    "url": f"{API_BASE}/releases/assets/101",
                },
                {
                    "name": "bsr-action-registry.sigstore.json",
                    "url": f"{API_BASE}/releases/assets/102",
                },
            ]
        ),
        f"{API_BASE}/releases/77/assets?per_page=100&page=2": _json_route([]),
        f"{API_BASE}/releases/assets/101": (record_raw, octet_stream),
        f"{API_BASE}/releases/assets/102": (b"signed-bundle", octet_stream),
        TAG_REF_URL: _json_route({"object": {"sha": tag_object, "type": "tag"}}),
        f"{API_BASE}/git/tags/{tag_object}": _json_route(
            {"object": {"sha": HEAD, "type": "commit"}}
        ),
        GHCR_TOKEN_URL: _json_route({"token": "ghcr-pull-token"}),
        MANIFEST_URL: (
            b"{}",
            {
                "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                "Docker-Content-Digest": IMAGE_DIGEST,
            },
        ),
        ATTESTATIONS_URL: _json_route(
            {"attestations": [{"repository_id": 123, "bundle_url": BUNDLE_URL}]}
        ),
        BUNDLE_URL: (bundle_raw, {"Content-Type": "application/json"}),
    }
    for index, (path, content) in enumerate(ACTION_BYTES.items(), start=10):
        routes[f"{API_BASE}/contents/{path}?ref={HEAD}"] = _json_route(
            {
                "sha": _blob_oid(index),
                "encoding": "base64",
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    return routes


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def test_cli_load_reads_registry_through_github_http_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsysbinary: pytest.CaptureFixture[bytes],
) -> None:
    bundle = _provenance_bundle()
    bundle_raw = _canonical(bundle)
    statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"]))
    source = _base_record()
    source["publisher"]["provenance_digest"] = _sha256(_canonical(statement))
    record = build_record(source)
    raw = canonical_record_bytes(record)
    expected_path = tmp_path / "expected.json"
    expected_path.write_bytes(raw)
    routes = _github_routes(raw, bundle_raw)
    opener = _RoutingOpener(routes)
    _install_opener(monkeypatch, opener)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    executed: list[tuple[str, str, float, bytes, bytes | None]] = []

    def fake_run(
        command: list[str], *, check: bool, capture_output: bool, timeout: float
    ) -> Any:
        assert check is False
        assert capture_output is True
        with open(command[command.index("--bundle") + 1], "rb") as stream:
            bundle_seen = stream.read()
        payload_seen = None
        if command[1] == "verify-blob":
            assert command[command.index("--certificate-identity") + 1] == IDENTITY
            with open(command[-1], "rb") as stream:
                payload_seen = stream.read()
        executed.append((command[0], command[1], timeout, bundle_seen, payload_seen))
        return registry_module.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(registry_module.subprocess, "run", fake_run)

    status = registry_module.main(
        [
            "load",
            "--expected",
            str(expected_path),
            "--repository",
            REPOSITORY,
            "--api-timeout",
            "1",
            "--cosign",
            "cosign-fixture",
            "--cosign-timeout",
            "5",
            "--now",
            "2026-08-30T01:00:00Z",
        ]
    )

    assert opener.unrouted == []
    assert status == 0
    loader_tuple: dict[str, Any] = {
        "generation": 1,
        "phase": "G1",
        "channel": "beta",
        "head_oid": HEAD,
        "head_hash": "b" * 64,
        "record_hash": record["record_hash"],
        "result_oid": HEAD,
        "result_hash": record["result_hash"],
        "version_action_sha": HEAD,
        "publish_action_sha": HEAD,
        "registry_action_sha": HEAD,
        "publisher_image_digest": IMAGE_DIGEST,
        "workflow_sha256": _sha256(ACTION_BYTES[".github/workflows/cd.yml"]),
        "advertisement_tag": "v1.6.0-beta.1",
    }
    loader_tuple["loader_output_sha256"] = _sha256(_canonical(loader_tuple))
    assert capsysbinary.readouterr().out == _canonical(loader_tuple) + b"\n"
    assert sorted(request.full_url for request in opener.requests) == sorted(routes)
    assert set(opener.timeouts) == {1}
    authorization = {
        request.full_url: request.unredirected_hdrs.get("Authorization")
        for request in opener.requests
    }
    assert authorization[RELEASES_URL] == "Bearer test-token"
    assert authorization[f"{API_BASE}/releases/assets/101"] == "Bearer test-token"
    assert authorization[GHCR_TOKEN_URL] is None
    assert authorization[MANIFEST_URL] == "Bearer ghcr-pull-token"
    assert authorization[BUNDLE_URL] is None
    accept = {
        request.full_url: request.get_header("Accept") for request in opener.requests
    }
    assert accept[f"{API_BASE}/releases/assets/101"] == "application/octet-stream"
    assert executed == [
        ("cosign-fixture", "verify-blob", 5.0, b"signed-bundle", raw),
        ("gh", "attestation", 30.0, bundle_raw, None),
    ]


def _read_releases(provider: Any) -> Any:
    return provider.list_releases(REPOSITORY, page=1, per_page=100)


def _read_assets(provider: Any) -> Any:
    return provider.list_release_assets(REPOSITORY, 77, page=1, per_page=100)


def _read_action_blob(provider: Any) -> Any:
    return provider.read_blob(REPOSITORY, "action.yml", HEAD)


def _peel_beta_tag(provider: Any) -> Any:
    return provider.peel_tag(REPOSITORY, "v1.6.0-beta.1")


def _read_publisher_manifest(provider: Any) -> Any:
    return provider.read_oci_manifest(
        "ghcr.io/n24q02m/better-semantic-release-publisher", IMAGE_DIGEST
    )


def _read_publisher_attestation(provider: Any) -> Any:
    return provider.read_attestation(REPOSITORY, IMAGE_DIGEST, "937309")


@pytest.mark.parametrize(
    "url,body,read,message",
    [
        pytest.param(
            RELEASES_URL, b"{}", _read_releases, "provider releases", id="releases"
        ),
        pytest.param(
            RELEASES_URL,
            b'[{"id":1,"id":2}]',
            _read_releases,
            "provider duplicate key",
            id="duplicate-key",
        ),
        pytest.param(RELEASES_URL, b"[", _read_releases, "provider json", id="json"),
        pytest.param(
            RELEASES_URL, b"[NaN]", _read_releases, "provider json", id="nan-constant"
        ),
        pytest.param(
            RELEASES_URL, b"\xff", _read_releases, "provider json", id="not-utf8"
        ),
        pytest.param(ASSETS_URL, b"{}", _read_assets, "provider assets", id="assets"),
        pytest.param(BLOB_URL, b"[]", _read_action_blob, "provider blob", id="blob"),
        pytest.param(
            TAG_REF_URL, b"[]", _peel_beta_tag, "provider tag", id="tag-array"
        ),
        pytest.param(
            TAG_REF_URL,
            b'{"object":[]}',
            _peel_beta_tag,
            "provider tag",
            id="tag-object-array",
        ),
        pytest.param(
            TAG_REF_URL,
            b'{"object":{"sha":1,"type":"commit"}}',
            _peel_beta_tag,
            "provider tag",
            id="tag-numeric-sha",
        ),
        pytest.param(
            TAG_REF_URL,
            b'{"object":{"sha":"abc","type":"tree"}}',
            _peel_beta_tag,
            "provider tag",
            id="tag-tree",
        ),
        pytest.param(
            GHCR_TOKEN_URL,
            b"[]",
            _read_publisher_manifest,
            "provider registry token",
            id="registry-token-array",
        ),
        pytest.param(
            GHCR_TOKEN_URL,
            b'{"token":""}',
            _read_publisher_manifest,
            "record provider registry token",
            id="registry-token-empty",
        ),
        pytest.param(
            ATTESTATIONS_URL,
            b"[]",
            _read_publisher_attestation,
            "provider attestation",
            id="attestations-array",
        ),
    ],
)
def test_http_provider_rejects_malformed_api_documents(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    body: bytes,
    read: Callable[[Any], Any],
    message: str,
) -> None:
    opener = _RoutingOpener({url: (body, {"Content-Type": "application/json"})})
    _install_opener(monkeypatch, opener)
    provider = registry_module.HttpRegistryProvider(token="test-token", timeout=1)

    with pytest.raises(RegistryError, match=_exact(message)):
        read(provider)
    assert [request.full_url for request in opener.requests] == [url]


def test_http_provider_bounds_annotated_tag_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    objects = [f"{index:x}" * 40 for index in range(1, 10)]
    routes: dict[str, Any] = {
        TAG_REF_URL: _json_route({"object": {"sha": objects[0], "type": "tag"}})
    }
    for index in range(8):
        routes[f"{API_BASE}/git/tags/{objects[index]}"] = _json_route(
            {"object": {"sha": objects[index + 1], "type": "tag"}}
        )
    opener = _RoutingOpener(routes)
    _install_opener(monkeypatch, opener)
    provider = registry_module.HttpRegistryProvider(timeout=1)

    with pytest.raises(RegistryError, match=_exact("provider tag depth")):
        _peel_beta_tag(provider)
    assert opener.unrouted == []
    assert len(opener.requests) == 9


def test_http_provider_requires_manifest_digest_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _RoutingOpener(
        {
            GHCR_TOKEN_URL: _json_route({"token": "ghcr-pull-token"}),
            MANIFEST_URL: (
                b"{}",
                {"Content-Type": "application/vnd.oci.image.manifest.v1+json"},
            ),
        }
    )
    _install_opener(monkeypatch, opener)
    provider = registry_module.HttpRegistryProvider(timeout=1)

    with pytest.raises(RegistryError, match=_exact("provider manifest digest")):
        _read_publisher_manifest(provider)
    assert [request.full_url for request in opener.requests] == [
        GHCR_TOKEN_URL,
        MANIFEST_URL,
    ]


@pytest.mark.parametrize(
    "failure,message",
    [
        pytest.param(
            urllib.error.HTTPError(RELEASES_URL, 403, "Forbidden", Message(), None),
            "provider http",
            id="http-error",
        ),
        pytest.param(
            urllib.error.URLError("name resolution failed"),
            "provider transport",
            id="url-error",
        ),
        pytest.param(
            TimeoutError("read timed out"), "provider transport", id="timeout"
        ),
    ],
)
def test_http_provider_normalizes_transport_failures(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException, message: str
) -> None:
    _install_opener(monkeypatch, _RoutingOpener({RELEASES_URL: failure}))
    provider = registry_module.HttpRegistryProvider(token="test-token", timeout=1)

    with pytest.raises(RegistryError, match=_exact(message)) as caught:
        _read_releases(provider)
    assert caught.value.__suppress_context__ is True


def test_http_provider_bounds_response_size(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = (b" " * (HTTP_LIMIT + 1), {"Content-Type": "application/json"})
    _install_opener(monkeypatch, _RoutingOpener({RELEASES_URL: oversized}))
    provider = registry_module.HttpRegistryProvider(timeout=1)

    with pytest.raises(RegistryError, match=_exact("provider response size")):
        _read_releases(provider)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/bsr-action-registry.json",
        "https://ghcr.io/v2/bsr-action-registry.json",
        "http://github.com/bsr-action-registry.json",
        "https://github.com:99999/bsr-action-registry.json",
        "https://user@github.com/bsr-action-registry.json",
        "https://github.com/bsr-action-registry.json#fragment",
    ],
)
def test_http_provider_rejects_asset_urls_outside_github(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    opener = _RoutingOpener({})
    _install_opener(monkeypatch, opener)
    provider = registry_module.HttpRegistryProvider(token="test-token", timeout=1)

    with pytest.raises(RegistryError, match=_exact("provider asset url")):
        provider.download_asset(url)
    assert opener.requests == []


@pytest.mark.parametrize(
    "kwargs,message",
    [
        pytest.param({"timeout": 0}, "provider timeout", id="timeout"),
        pytest.param(
            {"attestation_verifier": "gh"},
            "provider attestation verifier",
            id="verifier",
        ),
    ],
)
def test_http_provider_rejects_invalid_configuration(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(RegistryError, match=_exact(message)):
        registry_module.HttpRegistryProvider(**kwargs)


def _bundle_row(bundle_url: str, repository_id: Any = 123) -> dict[str, Any]:
    return {"repository_id": repository_id, "bundle_url": bundle_url}


_BLOB_HOST = "https://tmaproduction.blob.core.windows.net"


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param({"bundle_url": BUNDLE_URL}, id="rows-object"),
        pytest.param(["attestation"], id="scalar-row"),
        pytest.param([_bundle_row(BUNDLE_URL, True)], id="bool-repository-id"),
        pytest.param([_bundle_row(BUNDLE_URL, 0)], id="zero-repository-id"),
        pytest.param([_bundle_row(BUNDLE_URL, "123")], id="text-repository-id"),
        pytest.param([_bundle_row(BUNDLE_URL, 999)], id="foreign-repository-id"),
        pytest.param(
            [_bundle_row(f"{_BLOB_HOST}/blobs/123/2026/08/937309.json.sn")],
            id="wrong-prefix",
        ),
        pytest.param(
            [_bundle_row(f"{_BLOB_HOST}/attestations/123//937309.json.sn")],
            id="empty-segment",
        ),
        pytest.param(
            [_bundle_row(f"{_BLOB_HOST}/attestations/123/937309.json.sn")],
            id="short-path",
        ),
        pytest.param(
            [_bundle_row(f"{_BLOB_HOST}/attestations/123/2026/937309.json")],
            id="not-snappy-json",
        ),
        pytest.param(
            [_bundle_row(f"{_BLOB_HOST}/attestations/123/2026/.json.sn")],
            id="empty-attestation-id",
        ),
        pytest.param(
            [
                _bundle_row(
                    "https://attacker.example/attestations/123/2026/937309.json.sn"
                )
            ],
            id="foreign-host",
        ),
    ],
)
def test_http_attestation_reader_rejects_untrusted_bundle_rows(
    monkeypatch: pytest.MonkeyPatch, rows: Any
) -> None:
    opener = _RoutingOpener({})
    _install_opener(monkeypatch, opener)
    provider = _AttestationHTTPProvider(rows)

    with pytest.raises(RegistryError, match=_exact("provider attestation")):
        _read_publisher_attestation(provider)
    assert opener.requests == []


def _read_bundle(
    monkeypatch: pytest.MonkeyPatch,
    bundle_raw: bytes,
    *,
    content_type: str = "application/json",
    verifier: Any = None,
) -> str:
    opener = _CapturingOpener(bundle_raw, content_type=content_type)
    monkeypatch.setattr(
        registry_module.urllib.request, "build_opener", lambda *_values: opener
    )
    provider = _AttestationHTTPProvider(
        [_bundle_row(BUNDLE_URL)], attestation_verifier=verifier
    )
    return _read_publisher_attestation(provider)


def _replace_statement(updates: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    def mutate(bundle: dict[str, Any]) -> None:
        envelope = bundle["dsseEnvelope"]
        statement = json.loads(base64.b64decode(envelope["payload"]))
        statement.update(updates)
        envelope["payload"] = base64.b64encode(json.dumps(statement).encode()).decode()

    return mutate


def _replace_envelope(updates: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    def mutate(bundle: dict[str, Any]) -> None:
        bundle["dsseEnvelope"].update(updates)

    return mutate


def _drop_envelope(bundle: dict[str, Any]) -> None:
    bundle["dsseEnvelope"] = []


@pytest.mark.parametrize(
    "mutate,message",
    [
        pytest.param(_drop_envelope, "provider attestation bundle", id="envelope"),
        pytest.param(
            _replace_envelope({"payloadType": "text/plain"}),
            "provider attestation bundle",
            id="payload-type",
        ),
        pytest.param(
            _replace_envelope({"signatures": []}),
            "provider attestation bundle",
            id="no-signatures",
        ),
        pytest.param(
            _replace_envelope({"signatures": ["AA=="]}),
            "provider attestation bundle",
            id="scalar-signature",
        ),
        pytest.param(
            _replace_envelope({"signatures": [{"sig": "!!!"}]}),
            "provider attestation signature",
            id="signature-not-base64",
        ),
        pytest.param(
            _replace_envelope({"payload": "!!!"}),
            "provider attestation payload",
            id="payload-not-base64",
        ),
        pytest.param(
            _replace_statement({"_type": "https://in-toto.io/Statement/v0.1"}),
            "provider attestation statement",
            id="statement-type",
        ),
        pytest.param(
            _replace_statement({"predicate": []}),
            "provider attestation statement",
            id="predicate-array",
        ),
        pytest.param(
            _replace_statement({"subject": []}),
            "provider attestation subject",
            id="no-subject",
        ),
        pytest.param(
            _replace_statement({"subject": ["publisher"]}),
            "provider attestation subject",
            id="scalar-subject",
        ),
        pytest.param(
            _replace_statement(
                {"subject": [{"name": "publisher", "digest": IMAGE_DIGEST}]}
            ),
            "provider attestation subject",
            id="scalar-digest",
        ),
    ],
)
def test_http_attestation_reader_rejects_malformed_provenance_bundle(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    bundle = _provenance_bundle()
    mutate(bundle)
    verified: list[bytes] = []

    with pytest.raises(RegistryError, match=_exact(message)):
        _read_bundle(
            monkeypatch,
            json.dumps(bundle).encode(),
            verifier=lambda bundle_bytes, *_args: verified.append(bundle_bytes),
        )
    assert verified == []


def test_http_attestation_reader_requires_json_or_snappy_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _provenance_bundle()
    statement = json.loads(base64.b64decode(bundle["dsseEnvelope"]["payload"]))
    bundle_raw = json.dumps(bundle).encode()

    assert _read_bundle(
        monkeypatch, bundle_raw, content_type="Application/JSON; charset=utf-8"
    ) == _sha256(_canonical(statement))
    with pytest.raises(
        RegistryError, match=_exact("provider attestation content type")
    ):
        _read_bundle(monkeypatch, bundle_raw, content_type="text/plain")


def test_http_attestation_reader_keeps_verifier_errors_and_redacts_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_raw = json.dumps(_provenance_bundle()).encode()

    def rejecting(*_args: Any) -> None:
        raise RegistryError("provider attestation verifier")

    def crashing(*_args: Any) -> None:
        raise RuntimeError("gh crashed while holding test-token")

    with pytest.raises(RegistryError, match=_exact("provider attestation verifier")):
        _read_bundle(monkeypatch, bundle_raw, verifier=rejecting)
    with pytest.raises(
        RegistryError, match=_exact("provider attestation signature")
    ) as caught:
        _read_bundle(monkeypatch, bundle_raw, verifier=crashing)
    assert caught.value.__suppress_context__ is True


def _subprocess_must_not_run(*_args: Any, **_kwargs: Any) -> Any:
    pytest.fail("verifier subprocess must not run for invalid invocation")


@pytest.mark.parametrize("gh_binary,timeout", [("", 30.0), ("gh", 0)])
def test_gh_attestation_verifier_rejects_invalid_invocation(
    monkeypatch: pytest.MonkeyPatch, gh_binary: str, timeout: float
) -> None:
    monkeypatch.setattr(registry_module.subprocess, "run", _subprocess_must_not_run)

    with pytest.raises(RegistryError, match=_exact("provider attestation verifier")):
        registry_module.verify_attestation_with_gh(
            b"{}", REPOSITORY, IMAGE_DIGEST, gh_binary=gh_binary, timeout=timeout
        )


def test_gh_attestation_verifier_normalizes_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match=_exact("provider attestation verifier")):
        registry_module.verify_attestation_with_gh(
            json.dumps(_provenance_bundle()).encode(),
            REPOSITORY,
            IMAGE_DIGEST,
            gh_binary=str(tmp_path / "missing-gh"),
        )


@pytest.mark.parametrize("identity,timeout", [("", 30.0), (IDENTITY, 0)])
def test_cosign_verifier_rejects_invalid_invocation(
    monkeypatch: pytest.MonkeyPatch, identity: str, timeout: float
) -> None:
    monkeypatch.setattr(registry_module.subprocess, "run", _subprocess_must_not_run)

    with pytest.raises(RegistryError, match=_exact("signature verifier")):
        registry_module.verify_with_cosign(
            b"{}\n", b"{}", identity=identity, timeout=timeout
        )


def test_cosign_verifier_normalizes_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match=_exact("signature verifier")):
        registry_module.verify_with_cosign(
            b"{}\n",
            b"{}",
            identity=IDENTITY,
            cosign_binary=str(tmp_path / "missing-cosign"),
        )


def test_cosign_verifier_rejects_failed_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def failing_run(command: list[str], **_kwargs: Any) -> Any:
        commands.append(command)
        return registry_module.subprocess.CompletedProcess(
            command, 1, stderr=b"no matching signatures"
        )

    monkeypatch.setattr(registry_module.subprocess, "run", failing_run)

    with pytest.raises(RegistryError, match=_exact("signature verification")):
        registry_module.verify_with_cosign(b"{}\n", b"{}", identity=IDENTITY)
    assert [command[:2] for command in commands] == [["cosign", "verify-blob"]]


@pytest.mark.parametrize(
    "compressed",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\x80", id="truncated-length"),
        pytest.param(b"\x80\x80\x80\x80\x80", id="overlong-length"),
        pytest.param(b"\x01" + bytes([60 << 2]), id="truncated-literal-length"),
        pytest.param(b"\x04\x01", id="truncated-one-byte-copy"),
        pytest.param(b"\x04\x02\x00", id="truncated-two-byte-copy"),
        pytest.param(b"\x01\x00a\x00b", id="data-after-complete-output"),
        pytest.param(b"\x05\x00a", id="short-output"),
    ],
)
def test_snappy_decoder_rejects_truncated_or_inconsistent_blocks(
    compressed: bytes,
) -> None:
    with pytest.raises(RegistryError, match=_exact("provider attestation compression")):
        registry_module._decode_snappy_block(compressed)


def test_snappy_decoder_rejects_declared_length_above_asset_bound() -> None:
    # Little-endian base-128 varint for 16 MiB + 1 (one byte past the asset bound).
    declared_length = bytes([0x81, 0x80, 0x80, 0x08])

    with pytest.raises(RegistryError, match=_exact("provider attestation size")):
        registry_module._decode_snappy_block(declared_length + b"\x00a")


def test_cli_generate_streams_stdin_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.TextIOWrapper(io.BytesIO(json.dumps(_base_record()).encode())),
    )

    assert registry_module.main(["generate"]) == 0
    assert capsysbinary.readouterr().out == canonical_record_bytes(
        build_record(_base_record())
    )


def test_cli_generate_treats_null_previous_as_chain_root(tmp_path: Path) -> None:
    source = _base_record()
    source["generation"] = 4
    source["previous"] = {"stale": "summary"}
    source_path = tmp_path / "source.json"
    previous_path = tmp_path / "previous.json"
    output_path = tmp_path / "registry.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    previous_path.write_bytes(b"null\n")

    status = registry_module.main(
        [
            "generate",
            "--input",
            str(source_path),
            "--previous",
            str(previous_path),
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    assert output_path.read_bytes() == canonical_record_bytes(
        build_record(_base_record())
    )


def test_cli_redacts_unreadable_input_and_unwritable_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_bytes(canonical_record_bytes(build_record(_base_record())))
    missing_input = ["verify", "--input", str(tmp_path / "missing.json")]
    directory_output = [
        "verify",
        "--input",
        str(record_path),
        "--output",
        str(tmp_path),
        "--now",
        "2026-08-30T01:00:00Z",
    ]

    assert registry_module.main(missing_input) == 2
    assert registry_module.main(directory_output) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "registry error\nregistry error\n"


def test_module_entrypoint_exits_with_cli_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = canonical_record_bytes(build_record(_base_record()))
    record_path = tmp_path / "record.json"
    output_path = tmp_path / "verified.json"
    record_path.write_bytes(raw)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "action_pin_registry",
            "verify",
            "--input",
            str(record_path),
            "--output",
            str(output_path),
            "--now",
            "2026-08-30T01:00:00Z",
        ],
    )

    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(registry_module.__file__), run_name="__main__")

    assert exited.value.code == 0
    assert output_path.read_bytes() == raw
