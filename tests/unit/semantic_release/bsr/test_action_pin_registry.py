from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from email.message import Message
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
