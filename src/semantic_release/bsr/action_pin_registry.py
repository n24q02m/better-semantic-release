"""
Canonical, read-only verification for the BSR action-pin registry.

The registry is deliberately implemented with only Python's standard library.  The
public functions in this module are used by the composite loader action as well as
by the offline generator and verifier in ``scripts/action_pin_registry.py``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import datetime as _datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

_SCHEMA_VERSION = "bsr-action-registry/v1"
_SIGSTORE_BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"
_IN_TOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json"
_IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
_SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
_TOP_KEYS = (
    "schema_version",
    "generation",
    "phase",
    "channel",
    "issued_at",
    "expires_at",
    "owner",
    "previous",
    "head_oid",
    "head_hash",
    "record_hash",
    "result_oid",
    "result_hash",
    "version_action",
    "publish_action",
    "registry_action",
    "publisher",
    "advertisement",
    "workflow",
)
_BUILD_KEYS = tuple(
    key for key in _TOP_KEYS if key not in {"record_hash", "result_oid", "result_hash"}
)
_IMMUTABLE_KEYS = (
    "generation",
    "previous",
    "head_oid",
    "head_hash",
    "version_action",
    "publish_action",
    "registry_action",
    "publisher",
    "workflow",
)
_ACTION_KEYS = ("repository", "path", "sha", "action_blob_oid", "action_sha256")
_PUBLISHER_KEYS = (
    "identity",
    "image_ref",
    "image_digest",
    "image_source_commit",
    "dockerfile_sha256",
    "runtime_sha256",
    "provenance_repository",
    "provenance_workflow",
    "provenance_attestation_id",
    "provenance_digest",
)
_ADVERTISEMENT_KEYS = (
    "repository",
    "tag",
    "target_sha",
    "release_url",
    "registry_asset",
    "signature_asset",
    "image_ref",
)
_WORKFLOW_KEYS = (
    "repository",
    "path",
    "release_commit",
    "blob_oid",
    "sha256",
    "environment",
)
_PREVIOUS_KEYS = (
    "generation",
    "head_oid",
    "record_hash",
    "result_oid",
    "result_hash",
    "advertisement_tag",
)
_TUPLE_KEYS = (
    "generation",
    "phase",
    "channel",
    "head_oid",
    "head_hash",
    "record_hash",
    "result_oid",
    "result_hash",
    "version_action_sha",
    "publish_action_sha",
    "registry_action_sha",
    "publisher_image_digest",
    "workflow_sha256",
    "advertisement_tag",
)

_MAX_RECORD_BYTES = 2 * 1024 * 1024
_MAX_ASSET_BYTES = 16 * 1024 * 1024
_MAX_HTTP_BYTES = 32 * 1024 * 1024
_MAX_PAGES = 256
_PAGE_SIZE = 100
_COSIGN_ISSUER = "https://token.actions.githubusercontent.com"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)\.[0-9]+)?$")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GHCR_PUBLISHER_PATH_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?/[a-z0-9](?:[a-z0-9_.-]*[a-z0-9])?-publisher$"
)


class RegistryError(Exception):
    """A malformed, stale, ambiguous, or unverifiable registry state."""


class RegistryProvider(Protocol):
    """Minimal read-only provider boundary used by :func:`load_remote_registry`."""

    def list_releases(
        self, repository: str, *, page: int, per_page: int
    ) -> list[Mapping[str, Any]]: ...

    def list_release_assets(
        self, repository: str, release_id: int, *, page: int, per_page: int
    ) -> list[Mapping[str, Any]]: ...

    def download_asset(self, url: str) -> bytes: ...

    def peel_tag(self, repository: str, tag: str) -> str: ...

    def read_blob(self, repository: str, path: str, sha: str) -> Mapping[str, Any]: ...

    def read_oci_manifest(self, image_ref: str, image_digest: str) -> str: ...

    def read_attestation(
        self, repository: str, subject_digest: str, attestation_id: str
    ) -> str: ...


SignatureVerifier = Callable[[bytes, bytes], Any]
AttestationVerifier = Callable[[bytes, str, str], Any]


class _DuplicateKey(ValueError):
    pass


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise ValueError


def _walk_for_controls(value: Any) -> None:
    if isinstance(value, str):
        if any(ord(char) < 0x20 for char in value):
            raise RegistryError("record control character")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RegistryError("record key")
            _walk_for_controls(key)
            _walk_for_controls(child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for child in value:
            _walk_for_controls(child)


def _walk_for_attestation_controls(
    value: Any,
    path: tuple[str | int, ...] = (),
) -> None:
    if isinstance(value, str):
        controls = {ord(char) for char in value if ord(char) < 0x20}
        checkpoint_envelope = (
            len(path) == 6
            and path[:2] == ("verificationMaterial", "tlogEntries")
            and isinstance(path[2], int)
            and path[3:] == ("inclusionProof", "checkpoint", "envelope")
        )
        if controls and (not checkpoint_envelope or controls - {0x0A}):
            raise RegistryError("record control character")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RegistryError("record key")
            _walk_for_controls(key)
            _walk_for_attestation_controls(child, (*path, key))
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for index, child in enumerate(value):
            _walk_for_attestation_controls(child, (*path, index))


def _decode_json(raw: bytes, *, max_bytes: int) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > max_bytes:
        raise RegistryError("record size")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RegistryError("record encoding") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        raise RegistryError("record duplicate key") from None
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RegistryError("record json") from None
    if not isinstance(value, Mapping):
        raise RegistryError("record object")
    return value


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise RegistryError("record canonical json") from None


def _parse_json(raw: bytes, *, max_bytes: int = _MAX_RECORD_BYTES) -> Mapping[str, Any]:
    value = _decode_json(raw, max_bytes=max_bytes)
    _walk_for_controls(value)
    return value


def _parse_attestation_json(raw: bytes) -> Mapping[str, Any]:
    value = _decode_json(raw, max_bytes=_MAX_ASSET_BYTES)
    _walk_for_attestation_controls(value)
    return value


def _exact_keys(value: Any, expected: Sequence[str], scope: str) -> None:
    if not isinstance(value, Mapping):
        raise RegistryError(f"record {scope} object")
    actual = set(value)
    expected_set = set(expected)
    if actual - expected_set:
        raise RegistryError(f"record {scope} unknown key")
    if expected_set - actual:
        raise RegistryError(f"record {scope} missing key")


def _mapping(value: Any, expected: Sequence[str], scope: str) -> Mapping[str, Any]:
    _exact_keys(value, expected, scope)
    if not isinstance(value, Mapping):
        raise RegistryError(f"record {scope} object")
    return value


def _text(value: Any, scope: str, *, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise RegistryError(f"record {scope}")
    if any(ord(char) < 0x20 for char in value):
        raise RegistryError(f"record {scope}")
    return value


def _sha256(value: Any, scope: str) -> str:
    digest = _text(value, scope, max_length=64)
    if _SHA256_RE.fullmatch(digest) is None:
        raise RegistryError(f"record {scope}")
    return digest


def _oid(value: Any, scope: str) -> str:
    oid = _text(value, scope, max_length=40)
    if _OID_RE.fullmatch(oid) is None:
        raise RegistryError(f"record {scope}")
    return oid


def _image_digest(value: Any, scope: str = "image digest") -> str:
    digest = _text(value, scope, max_length=71)
    if _IMAGE_DIGEST_RE.fullmatch(digest) is None:
        raise RegistryError(f"record {scope}")
    return digest


def _repository(value: Any, scope: str = "repository") -> str:
    repository = _text(value, scope, max_length=200)
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise RegistryError(f"record {scope}")
    return repository


def _timestamp(value: Any, scope: str) -> _datetime.datetime:
    timestamp = _text(value, scope, max_length=20)
    if _TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise RegistryError(f"record {scope}")
    try:
        parsed = _datetime.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise RegistryError(f"record {scope}") from None
    return parsed.replace(tzinfo=_datetime.timezone.utc)


def _now(value: _datetime.datetime | None) -> _datetime.datetime:
    current = (
        value if value is not None else _datetime.datetime.now(_datetime.timezone.utc)
    )
    if current.tzinfo is None or current.utcoffset() is None:
        raise RegistryError("record time")
    return current.astimezone(_datetime.timezone.utc)


def _generation(value: Any, scope: str = "generation") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RegistryError(f"record {scope}")
    return value


def _validate_tag(value: Any) -> str:
    tag = _text(value, "tag", max_length=255)
    if _TAG_RE.fullmatch(tag) is None:
        raise RegistryError("record tag")
    return tag


def _validate_action(
    value: Any, *, name: str, repository: str, head_oid: str, path: str
) -> None:
    action = _mapping(value, _ACTION_KEYS, name)
    if _repository(action["repository"], f"{name} repository") != repository:
        raise RegistryError(f"record {name} repository")
    if _text(action["path"], f"{name} path", max_length=255) != path:
        raise RegistryError(f"record {name} path")
    if _oid(action["sha"], f"{name} sha") != head_oid:
        raise RegistryError(f"record {name} sha")
    _oid(action["action_blob_oid"], f"{name} blob oid")
    _sha256(action["action_sha256"], f"{name} sha256")


def _validate_previous(value: Any, generation: int) -> None:
    if value is None:
        if generation != 1:
            raise RegistryError("record previous generation")
        return
    previous = _mapping(value, _PREVIOUS_KEYS, "previous")
    previous_generation = _generation(previous["generation"], "previous generation")
    if previous_generation != generation - 1:
        raise RegistryError("record previous generation")
    _oid(previous["head_oid"], "previous head oid")
    _sha256(previous["record_hash"], "previous record hash")
    _oid(previous["result_oid"], "previous result oid")
    _sha256(previous["result_hash"], "previous result hash")
    _validate_tag(previous["advertisement_tag"])


def _validate_lifecycle(
    record: Mapping[str, Any], now: _datetime.datetime | None
) -> tuple[str, str]:
    if (
        _text(record["schema_version"], "schema version", max_length=64)
        != _SCHEMA_VERSION
    ):
        raise RegistryError("record schema version")
    generation = _generation(record["generation"])
    phase = _text(record["phase"], "phase", max_length=8)
    channel = _text(record["channel"], "channel", max_length=16)
    if (phase, channel) not in {("G1", "beta"), ("G2", "stable")}:
        raise RegistryError("record phase channel")
    issued_at = _timestamp(record["issued_at"], "issued at")
    expires_at = _timestamp(record["expires_at"], "expires at")
    if expires_at <= issued_at:
        raise RegistryError("record expiry")
    if now is not None:
        current = _now(now)
        if current < issued_at:
            raise RegistryError("record not issued")
        if current >= expires_at:
            raise RegistryError("record expired")
    _text(record["owner"], "owner", max_length=255)
    _validate_previous(record["previous"], generation)
    return phase, channel


def _validate_action_set(
    record: Mapping[str, Any], expected_repository: str | None
) -> tuple[str, str]:
    head_oid = _oid(record["head_oid"], "head oid")
    _sha256(record["head_hash"], "head hash")
    version_action = _mapping(record["version_action"], _ACTION_KEYS, "version action")
    repository = _repository(version_action["repository"], "repository")
    if expected_repository is not None and repository != expected_repository:
        raise RegistryError("record repository")
    for field, name, path in (
        ("version_action", "version action", "action.yml"),
        ("publish_action", "publish action", "publish-action/action.yml"),
        ("registry_action", "registry action", "registry-action/action.yml"),
    ):
        _validate_action(
            record[field],
            name=name,
            repository=repository,
            head_oid=head_oid,
            path=path,
        )
    return repository, head_oid


def _validate_attestation_id(value: Any) -> str:
    attestation_id = _text(value, "provenance attestation id", max_length=32)
    if not attestation_id.isascii() or not attestation_id.isdigit():
        raise RegistryError("record provenance attestation id")
    if int(attestation_id) <= 0:
        raise RegistryError("record provenance attestation id")
    return attestation_id


def _validate_publisher(
    record: Mapping[str, Any], repository: str, head_oid: str
) -> tuple[str, str]:
    publisher = _mapping(record["publisher"], _PUBLISHER_KEYS, "publisher")
    identity = _text(publisher["identity"], "publisher identity", max_length=512)
    expected_identity = (
        f"https://github.com/{repository}/.github/workflows/cd.yml@refs/heads/main"
    )
    if identity != expected_identity:
        raise RegistryError("record publisher identity")
    image_ref = _text(publisher["image_ref"], "publisher image ref", max_length=512)
    expected_image_ref = f"ghcr.io/{repository.lower()}-publisher"
    if image_ref != expected_image_ref:
        raise RegistryError("record publisher image ref")
    image_digest = _image_digest(publisher["image_digest"])
    if _oid(publisher["image_source_commit"], "publisher source commit") != head_oid:
        raise RegistryError("record publisher source commit")
    _sha256(publisher["dockerfile_sha256"], "publisher dockerfile sha256")
    _sha256(publisher["runtime_sha256"], "publisher runtime sha256")
    if (
        _repository(publisher["provenance_repository"], "provenance repository")
        != repository
    ):
        raise RegistryError("record provenance repository")
    if (
        _text(publisher["provenance_workflow"], "provenance workflow", max_length=255)
        != ".github/workflows/cd.yml"
    ):
        raise RegistryError("record provenance workflow")
    _validate_attestation_id(publisher["provenance_attestation_id"])
    _sha256(publisher["provenance_digest"], "provenance digest")
    return image_ref, image_digest


def _validate_advertisement(
    record: Mapping[str, Any],
    repository: str,
    head_oid: str,
    channel: str,
    image_ref: str,
    image_digest: str,
) -> None:
    advertisement = _mapping(
        record["advertisement"], _ADVERTISEMENT_KEYS, "advertisement"
    )
    if (
        _repository(advertisement["repository"], "advertisement repository")
        != repository
    ):
        raise RegistryError("record advertisement repository")
    tag = _validate_tag(advertisement["tag"])
    if (channel == "beta" and "-" not in tag) or (channel == "stable" and "-" in tag):
        raise RegistryError("record phase tag")
    if _oid(advertisement["target_sha"], "advertisement target sha") != head_oid:
        raise RegistryError("record advertisement target")
    release_url = _text(advertisement["release_url"], "release url", max_length=1024)
    if release_url != f"https://github.com/{repository}/releases/tag/{tag}":
        raise RegistryError("record release url")
    if (
        _text(advertisement["registry_asset"], "registry asset", max_length=255)
        != "bsr-action-registry.json"
    ):
        raise RegistryError("record registry asset")
    if (
        _text(advertisement["signature_asset"], "signature asset", max_length=255)
        != "bsr-action-registry.sigstore.json"
    ):
        raise RegistryError("record signature asset")
    advertised_image = _text(
        advertisement["image_ref"], "advertisement image ref", max_length=1024
    )
    if advertised_image != f"{image_ref}@{image_digest}":
        raise RegistryError("record advertisement image ref")


def _validate_workflow(
    record: Mapping[str, Any], repository: str, head_oid: str, phase: str
) -> None:
    workflow = _mapping(record["workflow"], _WORKFLOW_KEYS, "workflow")
    if _repository(workflow["repository"], "workflow repository") != repository:
        raise RegistryError("record workflow repository")
    if (
        _text(workflow["path"], "workflow path", max_length=255)
        != ".github/workflows/cd.yml"
    ):
        raise RegistryError("record workflow path")
    if _oid(workflow["release_commit"], "workflow release commit") != head_oid:
        raise RegistryError("record workflow release commit")
    _oid(workflow["blob_oid"], "workflow blob oid")
    _sha256(workflow["sha256"], "workflow sha256")
    expected_environment = "beta-publish" if phase == "G1" else "stable-publish"
    if (
        _text(workflow["environment"], "workflow environment", max_length=255)
        != expected_environment
    ):
        raise RegistryError("record workflow environment")


def _validate_derived_fields(
    record: Mapping[str, Any], head_oid: str, check_hashes: bool
) -> None:
    if _oid(record["result_oid"], "result oid") != head_oid:
        raise RegistryError("record result oid")
    _sha256(record["record_hash"], "record hash")
    _sha256(record["result_hash"], "result hash")
    if not check_hashes:
        return
    if record["record_hash"] != _record_hash(record):
        raise RegistryError("record hash")
    if record["result_hash"] != _result_hash(record):
        raise RegistryError("result hash")


def _validate_record_shape(
    value: Any,
    *,
    now: _datetime.datetime | None = None,
    expected_repository: str | None = None,
    check_hashes: bool,
) -> dict[str, Any]:
    record = dict(_mapping(value, _TOP_KEYS, ""))
    _walk_for_controls(record)
    phase, channel = _validate_lifecycle(record, now)
    repository, head_oid = _validate_action_set(record, expected_repository)
    image_ref, image_digest = _validate_publisher(record, repository, head_oid)
    _validate_advertisement(
        record, repository, head_oid, channel, image_ref, image_digest
    )
    _validate_workflow(record, repository, head_oid, phase)
    _validate_derived_fields(record, head_oid, check_hashes)
    return record


def _record_hash(record: Mapping[str, Any]) -> str:
    immutable = {key: record[key] for key in _IMMUTABLE_KEYS}
    return hashlib.sha256(_canonical_json(immutable)).hexdigest()


def _result_hash(record: Mapping[str, Any]) -> str:
    result = {key: value for key, value in record.items() if key != "result_hash"}
    return hashlib.sha256(_canonical_json(result)).hexdigest()


def build_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build one canonical registry record and calculate all derived fields."""
    if not isinstance(record, Mapping):
        raise RegistryError("record object")
    _walk_for_controls(record)
    actual = set(record)
    expected = set(_BUILD_KEYS) | {"record_hash", "result_oid", "result_hash"}
    if actual - expected:
        raise RegistryError("record unknown key")
    if set(_BUILD_KEYS) - actual:
        raise RegistryError("record missing key")
    base = copy.deepcopy(dict(record))
    # A caller may pass stale derived fields while regenerating a record.  They
    # are never trusted and are always replaced from the immutable source tuple.
    base.pop("record_hash", None)
    base.pop("result_oid", None)
    base.pop("result_hash", None)
    _validate_record_shape(
        {
            **base,
            "record_hash": "0" * 64,
            "result_oid": base.get("head_oid"),
            "result_hash": "0" * 64,
        },
        check_hashes=False,
    )
    base["record_hash"] = _record_hash(
        {
            **base,
            "record_hash": "0" * 64,
            "result_oid": base["head_oid"],
            "result_hash": "0" * 64,
        }
    )
    base["result_oid"] = base["head_oid"]
    base["result_hash"] = _result_hash(base)
    return _validate_record_shape(base, check_hashes=True)


def canonical_record_bytes(record: Mapping[str, Any]) -> bytes:
    """Return canonical newline-terminated UTF-8 bytes for a verified record."""
    checked = _validate_record_shape(record, check_hashes=True)
    return _canonical_json(checked) + b"\n"


def verify_record(
    raw: bytes, *, now: _datetime.datetime | None = None
) -> dict[str, Any]:
    """Parse and verify one exact canonical registry document."""
    parsed = _parse_json(raw)
    canonical = _canonical_json(parsed) + b"\n"
    if raw != canonical:
        raise RegistryError("record canonical bytes")
    return _validate_record_shape(parsed, now=now, check_hashes=True)


def _previous_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    advertisement = cast(Mapping[str, Any], record["advertisement"])
    return {
        "generation": record["generation"],
        "head_oid": record["head_oid"],
        "record_hash": record["record_hash"],
        "result_oid": record["result_oid"],
        "result_hash": record["result_hash"],
        "advertisement_tag": advertisement["tag"],
    }


def build_chained_record(
    source: Mapping[str, Any],
    previous: Mapping[str, Any] | bytes | None,
    *,
    now: _datetime.datetime | None = None,
) -> dict[str, Any]:
    """Build the initial record or the sole successor of a verified chain head."""
    current = copy.deepcopy(dict(source))
    if previous is None:
        current["generation"] = 1
        current["previous"] = None
        return build_record(current)
    checked_previous = (
        verify_record(previous, now=now)
        if isinstance(previous, bytes)
        else verify_record(canonical_record_bytes(previous), now=now)
    )
    if checked_previous["phase"] == "G2" and current.get("phase") == "G1":
        raise RegistryError("phase chain")
    current["generation"] = checked_previous["generation"] + 1
    current["previous"] = _previous_summary(checked_previous)
    return build_record(current)


def _bounded_record_list(
    records: Iterable[Mapping[str, Any] | bytes],
) -> list[Mapping[str, Any] | bytes]:
    materialized: list[Mapping[str, Any] | bytes] = []
    for item in records:
        if len(materialized) >= _MAX_PAGES * _PAGE_SIZE:
            raise RegistryError("record collection")
        materialized.append(item)
    if not materialized:
        raise RegistryError("record collection")
    return materialized


def _verify_same_repository(
    records: Sequence[Mapping[str, Any] | bytes],
) -> list[dict[str, Any]]:
    verified = [
        verify_record(item)
        if isinstance(item, bytes)
        else verify_record(canonical_record_bytes(item))
        for item in records
    ]
    repositories = {
        cast(str, cast(Mapping[str, Any], item["advertisement"])["repository"])
        for item in verified
    }
    if len(repositories) != 1:
        raise RegistryError("record repository fork")
    return verified


def _validate_chain(records: Sequence[Mapping[str, Any]]) -> None:
    for left, right in zip(records, records[1:]):
        if left["generation"] == right["generation"]:
            if _canonical_json(left) == _canonical_json(right):
                raise RegistryError("duplicate generation")
            raise RegistryError("competing generation")
    if records[0]["previous"] is not None:
        raise RegistryError("previous chain missing predecessor")
    for predecessor, current in zip(records, records[1:]):
        if current["generation"] != predecessor["generation"] + 1:
            raise RegistryError("previous chain missing predecessor")
        if current["previous"] != _previous_summary(predecessor):
            raise RegistryError("previous chain mismatch")
        if predecessor["phase"] == "G2" and current["phase"] == "G1":
            raise RegistryError("phase chain")


def select_latest(
    records: Iterable[Mapping[str, Any] | bytes],
    *,
    now: _datetime.datetime | None = None,
) -> dict[str, Any]:
    """Verify a bounded collection and select its sole highest chain member."""
    verified = _verify_same_repository(_bounded_record_list(records))
    verified.sort(key=lambda item: cast(int, item["generation"]))
    _validate_chain(verified)
    return _validate_record_shape(verified[-1], now=now, check_hashes=True)


def _provider_call(provider: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    function = getattr(provider, method, None)
    if not callable(function):
        raise RegistryError("provider method")
    try:
        return function(*args, **kwargs)
    except RegistryError:
        raise
    except Exception:  # noqa: BLE001 - provider failures are normalized and redacted.
        raise RegistryError("provider read") from None


def _bounded_pages(
    provider: Any, method: str, *args: Any
) -> Iterable[Mapping[str, Any]]:
    for page in range(1, _MAX_PAGES + 1):
        page_value = _provider_call(
            provider, method, *args, page=page, per_page=_PAGE_SIZE
        )
        if not isinstance(page_value, list) or len(page_value) > _PAGE_SIZE:
            raise RegistryError("provider pagination")
        if not page_value:
            return
        for item in page_value:
            if not isinstance(item, Mapping):
                raise RegistryError("provider response")
            yield item
    raise RegistryError("provider pagination bound")


def _asset_name(value: Mapping[str, Any]) -> str:
    return _text(value.get("name"), "asset name", max_length=255)


def _asset_url(value: Mapping[str, Any]) -> str:
    url = value.get("url", value.get("browser_download_url"))
    return _text(url, "asset url", max_length=4096)


def _bytes_value(value: Any, scope: str, *, max_bytes: int = _MAX_ASSET_BYTES) -> bytes:
    if isinstance(value, bytes):
        if len(value) > max_bytes:
            raise RegistryError(f"record {scope} size")
        return value
    raise RegistryError(f"record {scope}")


def _blob_bytes(value: Mapping[str, Any]) -> tuple[str, bytes]:
    oid = _oid(value.get("oid", value.get("sha")), "blob oid")
    if "bytes" in value:
        raw = _bytes_value(value["bytes"], "blob")
    elif "content" in value:
        content = value["content"]
        encoding = value.get("encoding", "base64")
        if not isinstance(content, str) or encoding != "base64":
            raise RegistryError("blob content")
        try:
            raw = base64.b64decode(content.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError):
            raise RegistryError("blob content") from None
        raw = _bytes_value(raw, "blob")
    else:
        raise RegistryError("blob content")
    return oid, raw


def _read_and_check_blob(
    provider: RegistryProvider,
    repository: str,
    path: str,
    sha: str,
    expected_oid: str,
    expected_sha256: str,
) -> bytes:
    value = _provider_call(provider, "read_blob", repository, path, sha)
    if not isinstance(value, Mapping):
        raise RegistryError("blob response")
    actual_oid, raw = _blob_bytes(value)
    if actual_oid != expected_oid:
        raise RegistryError("blob oid")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RegistryError("blob sha256")
    return raw


def _read_and_check_sha256(
    provider: RegistryProvider,
    repository: str,
    path: str,
    sha: str,
    expected_sha256: str,
) -> None:
    value = _provider_call(provider, "read_blob", repository, path, sha)
    if not isinstance(value, Mapping):
        raise RegistryError("publisher source")
    _oid_value, raw = _blob_bytes(value)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RegistryError("publisher source")


def _extract_publisher_image(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RegistryError("publisher action encoding") from None
    # This is intentionally a small YAML boundary, not a YAML parser.  The
    # Docker action contract has one scalar ``image`` property; accepting only
    # a complete scalar prevents tag fallback and expression interpolation.
    image_line = re.compile(
        r"^[ \t]*image:[ \t]*(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))[ \t]*(?:#[^\r\n]*)?\r?$",
        re.MULTILINE,
    )
    found = [
        next(group for group in match.groups() if group is not None)
        for match in image_line.finditer(text)
    ]
    if len(found) != 1:
        raise RegistryError("publisher action image")
    image = found[0]
    if not image.startswith("docker://") or "@sha256:" not in image:
        raise RegistryError("publisher action image")
    return image


def _fresh_readback(
    provider: RegistryProvider, record: Mapping[str, Any], repository: str
) -> None:
    advertisement = cast(Mapping[str, Any], record["advertisement"])
    tag = cast(str, advertisement["tag"])
    peeled = _provider_call(provider, "peel_tag", repository, tag)
    if not isinstance(peeled, str) or peeled != record["head_oid"]:
        raise RegistryError("tag target")

    actions = (
        ("version_action", "action.yml"),
        ("publish_action", "publish-action/action.yml"),
        ("registry_action", "registry-action/action.yml"),
    )
    publish_raw: bytes | None = None
    for field, path in actions:
        descriptor = cast(Mapping[str, Any], record[field])
        raw = _read_and_check_blob(
            provider,
            repository,
            path,
            cast(str, descriptor["sha"]),
            cast(str, descriptor["action_blob_oid"]),
            cast(str, descriptor["action_sha256"]),
        )
        if field == "publish_action":
            publish_raw = raw
    if publish_raw is None:
        raise RegistryError("publisher action")
    publisher = cast(Mapping[str, Any], record["publisher"])
    image = _extract_publisher_image(publish_raw)
    expected_image = f"docker://{publisher['image_ref']}@{publisher['image_digest']}"
    if image != expected_image:
        raise RegistryError("publisher action image")
    for path, digest_field in (
        ("publish-action/Dockerfile", "dockerfile_sha256"),
        ("src/semantic_release/bsr/release_publisher.py", "runtime_sha256"),
    ):
        _read_and_check_sha256(
            provider,
            repository,
            path,
            cast(str, publisher["image_source_commit"]),
            cast(str, publisher[digest_field]),
        )

    workflow = cast(Mapping[str, Any], record["workflow"])
    _read_and_check_blob(
        provider,
        repository,
        cast(str, workflow["path"]),
        cast(str, workflow["release_commit"]),
        cast(str, workflow["blob_oid"]),
        cast(str, workflow["sha256"]),
    )

    manifest = _provider_call(
        provider,
        "read_oci_manifest",
        cast(str, publisher["image_ref"]),
        cast(str, publisher["image_digest"]),
    )
    if not isinstance(manifest, str) or manifest != publisher["image_digest"]:
        raise RegistryError("oci manifest")
    attestation = _provider_call(
        provider,
        "read_attestation",
        repository,
        cast(str, publisher["image_digest"]),
        cast(str, publisher["provenance_attestation_id"]),
    )
    if (
        not isinstance(attestation, str)
        or attestation != publisher["provenance_digest"]
    ):
        raise RegistryError("attestation")


def _expected_record(
    expected: Mapping[str, Any] | bytes, *, now: _datetime.datetime | None
) -> dict[str, Any]:
    if isinstance(expected, bytes):
        return verify_record(expected, now=now)
    if not isinstance(expected, Mapping):
        raise RegistryError("expected record")
    return verify_record(canonical_record_bytes(expected), now=now)


def _loader_tuple(record: Mapping[str, Any]) -> dict[str, Any]:
    version_action = cast(Mapping[str, Any], record["version_action"])
    publish_action = cast(Mapping[str, Any], record["publish_action"])
    registry_action = cast(Mapping[str, Any], record["registry_action"])
    publisher = cast(Mapping[str, Any], record["publisher"])
    workflow = cast(Mapping[str, Any], record["workflow"])
    advertisement = cast(Mapping[str, Any], record["advertisement"])
    return {
        "generation": record["generation"],
        "phase": record["phase"],
        "channel": record["channel"],
        "head_oid": record["head_oid"],
        "head_hash": record["head_hash"],
        "record_hash": record["record_hash"],
        "result_oid": record["result_oid"],
        "result_hash": record["result_hash"],
        "version_action_sha": version_action["sha"],
        "publish_action_sha": publish_action["sha"],
        "registry_action_sha": registry_action["sha"],
        "publisher_image_digest": publisher["image_digest"],
        "workflow_sha256": workflow["sha256"],
        "advertisement_tag": advertisement["tag"],
    }


def _release_identifier(release: Mapping[str, Any], seen: set[int]) -> int:
    release_id = release.get("id")
    if (
        isinstance(release_id, bool)
        or not isinstance(release_id, int)
        or release_id <= 0
    ):
        raise RegistryError("release id")
    if release_id in seen:
        raise RegistryError("release duplicate")
    seen.add(release_id)
    return release_id


def _release_assets(
    provider: RegistryProvider, repository: str, release_id: int
) -> dict[str, str]:
    assets: dict[str, str] = {}
    for asset in _bounded_pages(
        provider, "list_release_assets", repository, release_id
    ):
        name = _asset_name(asset)
        if name in assets:
            raise RegistryError("asset duplicate")
        assets[name] = _asset_url(asset)
    return assets


def _verify_signature(
    verifier: SignatureVerifier, record_bytes: bytes, bundle_bytes: bytes
) -> None:
    try:
        verified = verifier(record_bytes, bundle_bytes)
    except RegistryError:
        raise
    except Exception:  # noqa: BLE001 - verifier failures are normalized and redacted.
        raise RegistryError("signature verification") from None
    if verified is False:
        raise RegistryError("signature verification")


def _registry_candidate(
    *,
    provider: RegistryProvider,
    signature_verifier: SignatureVerifier,
    repository: str,
    release: Mapping[str, Any],
    seen_release_ids: set[int],
) -> dict[str, Any] | None:
    release_id = _release_identifier(release, seen_release_ids)
    release_tag = _text(release.get("tag_name"), "release tag", max_length=255)
    release_url = _text(release.get("html_url"), "release url", max_length=1024)
    assets = _release_assets(provider, repository, release_id)
    registry_name = "bsr-action-registry.json"
    signature_name = "bsr-action-registry.sigstore.json"
    registry_url = assets.get(registry_name)
    signature_url = assets.get(signature_name)
    if (registry_url is None) != (signature_url is None):
        raise RegistryError("asset pair")
    if registry_url is None or signature_url is None:
        return None
    record_bytes = _bytes_value(
        _provider_call(provider, "download_asset", registry_url),
        "registry asset",
    )
    bundle_bytes = _bytes_value(
        _provider_call(provider, "download_asset", signature_url),
        "signature bundle",
    )
    candidate = verify_record(record_bytes)
    advertisement = _mapping(
        candidate["advertisement"], _ADVERTISEMENT_KEYS, "advertisement"
    )
    expected = (
        repository,
        release_tag,
        release_url,
        registry_name,
        signature_name,
    )
    actual = (
        advertisement["repository"],
        advertisement["tag"],
        advertisement["release_url"],
        advertisement["registry_asset"],
        advertisement["signature_asset"],
    )
    if actual != expected:
        raise RegistryError("advertisement")
    _verify_signature(signature_verifier, record_bytes, bundle_bytes)
    return candidate


def _collect_remote_candidates(
    provider: RegistryProvider,
    signature_verifier: SignatureVerifier,
    repository: str,
) -> list[dict[str, Any]]:
    seen_release_ids: set[int] = set()
    return [
        candidate
        for release in _bounded_pages(provider, "list_releases", repository)
        if (
            candidate := _registry_candidate(
                provider=provider,
                signature_verifier=signature_verifier,
                repository=repository,
                release=release,
                seen_release_ids=seen_release_ids,
            )
        )
        is not None
    ]


def _remote_candidates(
    provider: RegistryProvider,
    signature_verifier: SignatureVerifier,
    repository: str,
) -> list[dict[str, Any]]:
    candidates = _collect_remote_candidates(
        provider,
        signature_verifier,
        repository,
    )
    if not candidates:
        raise RegistryError("registry absent")
    return candidates


def load_remote_chain_head(
    *,
    provider: RegistryProvider,
    signature_verifier: SignatureVerifier,
    repository: str,
    now: _datetime.datetime | None = None,
) -> dict[str, Any] | None:
    """Verify the remote signed chain and return its latest record, if present."""
    repository = _repository(repository, "repository")
    if not callable(signature_verifier):
        raise RegistryError("signature verifier")
    candidates = _collect_remote_candidates(
        provider,
        signature_verifier,
        repository,
    )
    return select_latest(candidates, now=now) if candidates else None


def load_remote_registry(
    *,
    expected: Mapping[str, Any] | bytes,
    provider: RegistryProvider,
    signature_verifier: SignatureVerifier,
    repository: str,
    now: _datetime.datetime | None = None,
) -> dict[str, Any]:
    """
    Load, verify, fresh-read, and summarize the latest remote registry record.

    The provider protocol exposes reads only. This function never creates or
    updates a release, asset, registry record, signature, credential, or token.
    """
    repository = _repository(repository, "repository")
    if not callable(signature_verifier):
        raise RegistryError("signature verifier")
    expected_record = _expected_record(expected, now=now)
    expected_advertisement = _mapping(
        expected_record["advertisement"], _ADVERTISEMENT_KEYS, "advertisement"
    )
    if expected_advertisement["repository"] != repository:
        raise RegistryError("expected repository")
    selected = select_latest(
        _remote_candidates(provider, signature_verifier, repository),
        now=now,
    )
    if (
        selected["phase"],
        selected["channel"],
    ) != (
        expected_record["phase"],
        expected_record["channel"],
    ):
        raise RegistryError("phase channel")
    if _canonical_json(selected) != _canonical_json(expected_record):
        raise RegistryError("expected tuple")
    _fresh_readback(provider, selected, repository)
    result = _loader_tuple(selected)
    result["loader_output_sha256"] = hashlib.sha256(_canonical_json(result)).hexdigest()
    return result


def _github_redirect_host(host: str | None) -> bool:
    return bool(
        host
        and (
            host in {"github.com", "ghcr.io"}
            or host.endswith((".github.com", ".ghcr.io", ".githubusercontent.com"))
        )
    )


def _github_asset_host(host: str | None) -> bool:
    return bool(
        host
        and (
            host == "github.com"
            or host.endswith((".github.com", ".githubusercontent.com"))
        )
    )


def _validated_https_url(
    url: str,
    host_policy: Callable[[str | None], bool],
    error: str,
) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise RegistryError(error) from None
    if (
        parsed.scheme != "https"
        or not host_policy(parsed.hostname)
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RegistryError(error)
    return parsed


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request:
        _validated_https_url(new_url, _github_redirect_host, "provider redirect")
        redirected = super().redirect_request(request, fp, code, msg, headers, new_url)
        if redirected is None:
            raise RegistryError("provider redirect")
        redirected.headers.pop("Authorization", None)
        redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request:
        del request, fp, code, msg, headers, new_url
        raise RegistryError("provider redirect")


def _bounded_response(response: Any) -> tuple[bytes, Mapping[str, str]]:
    data = response.read(_MAX_HTTP_BYTES + 1)
    if len(data) > _MAX_HTTP_BYTES:
        raise RegistryError("provider response size")
    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    return data, headers


def _open_bounded(
    opener: Any,
    request: urllib.request.Request,
    timeout: float,
    *,
    http_error: str,
    transport_error: str,
) -> tuple[bytes, Mapping[str, str]]:
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            data, headers = _bounded_response(response)
    except RegistryError:
        raise
    except urllib.error.HTTPError:
        raise RegistryError(http_error) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RegistryError(transport_error) from None
    return data, headers


def _matching_attestation_bundle_url(row: Any, attestation_id: str) -> str | None:
    if not isinstance(row, Mapping):
        raise RegistryError("provider attestation")
    repository_id = row.get("repository_id")
    if (
        isinstance(repository_id, bool)
        or not isinstance(repository_id, int)
        or repository_id <= 0
    ):
        raise RegistryError("provider attestation")
    bundle_url = _text(row.get("bundle_url"), "attestation bundle url", max_length=4096)
    parsed = _validated_https_url(
        bundle_url,
        lambda host: host == "tmaproduction.blob.core.windows.net",
        "provider attestation",
    )
    parts = urllib.parse.unquote(parsed.path).split("/")
    if (
        len(parts) < 5
        or parts[:2] != ["", "attestations"]
        or parts[2] != str(repository_id)
        or any(not part for part in parts[3:])
    ):
        raise RegistryError("provider attestation")
    filename = parts[-1]
    suffix = ".json.sn"
    if not filename.endswith(suffix) or filename == suffix:
        raise RegistryError("provider attestation")
    return bundle_url if filename[: -len(suffix)] == attestation_id else None


def _select_attestation_bundle_url(value: Any, attestation_id: str) -> str:
    if not isinstance(value, Mapping):
        raise RegistryError("provider attestation")
    rows = value.get("attestations")
    if not isinstance(rows, list):
        raise RegistryError("provider attestation")
    matches = [
        match
        for row in rows
        if (match := _matching_attestation_bundle_url(row, attestation_id)) is not None
    ]
    if len(matches) != 1:
        raise RegistryError("provider attestation")
    return matches[0]


def _snappy_varint(raw: bytes, index: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 35, 7):
        if index >= len(raw):
            raise RegistryError("provider attestation compression")
        byte = raw[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            if value > _MAX_ASSET_BYTES:
                raise RegistryError("provider attestation size")
            return value, index
    raise RegistryError("provider attestation compression")


def _snappy_literal_length(tag: int, raw: bytes, index: int) -> tuple[int, int]:
    encoded_length = tag >> 2
    if encoded_length < 60:
        return encoded_length + 1, index
    width = encoded_length - 59
    end = index + width
    if end > len(raw):
        raise RegistryError("provider attestation compression")
    return int.from_bytes(raw[index:end], "little") + 1, end


def _snappy_copy(tag: int, raw: bytes, index: int) -> tuple[int, int, int]:
    kind = tag & 0x03
    if kind == 1:
        if index >= len(raw):
            raise RegistryError("provider attestation compression")
        length = 4 + ((tag >> 2) & 0x07)
        offset = ((tag & 0xE0) << 3) | raw[index]
        return length, offset, index + 1
    width = 2 if kind == 2 else 4
    end = index + width
    if end > len(raw):
        raise RegistryError("provider attestation compression")
    length = (tag >> 2) + 1
    return length, int.from_bytes(raw[index:end], "little"), end


def _append_snappy_copy(
    output: bytearray, *, length: int, offset: int, expected_length: int
) -> None:
    if offset <= 0 or offset > len(output) or len(output) + length > expected_length:
        raise RegistryError("provider attestation compression")
    remaining = length
    while remaining:
        start = len(output) - offset
        chunk_length = min(remaining, offset)
        output.extend(output[start : start + chunk_length])
        remaining -= chunk_length


def _decode_snappy_block(raw: bytes) -> bytes:
    if not raw or len(raw) > _MAX_HTTP_BYTES:
        raise RegistryError("provider attestation compression")
    expected_length, index = _snappy_varint(raw, 0)
    output = bytearray()
    while index < len(raw):
        if len(output) >= expected_length:
            raise RegistryError("provider attestation compression")
        tag = raw[index]
        index += 1
        if tag & 0x03 == 0:
            length, index = _snappy_literal_length(tag, raw, index)
            end = index + length
            if end > len(raw) or len(output) + length > expected_length:
                raise RegistryError("provider attestation compression")
            output.extend(raw[index:end])
            index = end
            continue
        length, offset, index = _snappy_copy(tag, raw, index)
        _append_snappy_copy(
            output,
            length=length,
            offset=offset,
            expected_length=expected_length,
        )
    if len(output) != expected_length:
        raise RegistryError("provider attestation compression")
    return bytes(output)


def _attestation_json_bytes(raw: bytes, headers: Mapping[str, str]) -> bytes:
    content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type == "application/x-snappy":
        return _decode_snappy_block(raw)
    if content_type == "application/json":
        return raw
    raise RegistryError("provider attestation content type")


def _decode_attestation_statement(bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    if bundle.get("mediaType") != _SIGSTORE_BUNDLE_MEDIA_TYPE or not isinstance(
        bundle.get("verificationMaterial"), Mapping
    ):
        raise RegistryError("provider attestation bundle")
    envelope = bundle.get("dsseEnvelope")
    if not isinstance(envelope, Mapping):
        raise RegistryError("provider attestation bundle")
    if envelope.get("payloadType") != _IN_TOTO_PAYLOAD_TYPE:
        raise RegistryError("provider attestation bundle")
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise RegistryError("provider attestation bundle")
    for signature in signatures:
        if not isinstance(signature, Mapping):
            raise RegistryError("provider attestation bundle")
        encoded_signature = _text(
            signature.get("sig"),
            "provider attestation signature",
            max_length=_MAX_ASSET_BYTES,
        )
        try:
            base64.b64decode(encoded_signature, validate=True)
        except (binascii.Error, ValueError):
            raise RegistryError("provider attestation signature") from None
    encoded_payload = _text(
        envelope.get("payload"),
        "provider attestation payload",
        max_length=_MAX_ASSET_BYTES,
    )
    try:
        payload = base64.b64decode(encoded_payload, validate=True)
    except (binascii.Error, ValueError):
        raise RegistryError("provider attestation payload") from None
    return _parse_json(payload, max_bytes=_MAX_ASSET_BYTES)


def _validate_attested_subject(
    statement: Mapping[str, Any], subject_digest: str
) -> None:
    if (
        statement.get("_type") != _IN_TOTO_STATEMENT_TYPE
        or statement.get("predicateType") != _SLSA_PROVENANCE_TYPE
        or not isinstance(statement.get("predicate"), Mapping)
    ):
        raise RegistryError("provider attestation statement")
    subjects = statement.get("subject")
    if not isinstance(subjects, list) or len(subjects) != 1:
        raise RegistryError("provider attestation subject")
    subject = subjects[0]
    if not isinstance(subject, Mapping):
        raise RegistryError("provider attestation subject")
    _text(subject.get("name"), "provider attestation subject name", max_length=4096)
    digest = subject.get("digest")
    if not isinstance(digest, Mapping):
        raise RegistryError("provider attestation subject")
    expected = subject_digest[len("sha256:") :]
    if digest.get("sha256") != expected:
        raise RegistryError("provider attestation subject")


def _validate_attestation_bundle(
    bundle: Mapping[str, Any], subject_digest: str
) -> Mapping[str, Any]:
    statement = _decode_attestation_statement(bundle)
    _validate_attested_subject(statement, subject_digest)
    return statement


def verify_attestation_with_gh(
    bundle: bytes,
    repository: str,
    subject_digest: str,
    *,
    gh_binary: str = "gh",
    timeout: float = 30.0,
) -> None:
    """Cryptographically verify one downloaded GitHub provenance bundle."""
    repository = _repository(repository, "repository")
    subject_digest = _image_digest(subject_digest, "subject digest")
    if not isinstance(gh_binary, str) or not gh_binary or timeout <= 0:
        raise RegistryError("provider attestation verifier")
    identity = (
        f"https://github.com/{repository}/.github/workflows/cd.yml@refs/heads/main"
    )
    image = f"oci://ghcr.io/{repository.lower()}-publisher@{subject_digest}"
    try:
        with tempfile.TemporaryDirectory(prefix="bsr-attestation-") as directory:
            bundle_path = os.path.join(directory, "bundle.json")
            with open(bundle_path, "wb") as bundle_file:
                bundle_file.write(
                    _bytes_value(
                        bundle,
                        "provider attestation bundle",
                        max_bytes=_MAX_ASSET_BYTES,
                    )
                )
            command = [
                gh_binary,
                "attestation",
                "verify",
                image,
                "--repo",
                repository,
                "--bundle",
                bundle_path,
                "--cert-identity",
                identity,
                "--cert-oidc-issuer",
                _COSIGN_ISSUER,
                "--predicate-type",
                _SLSA_PROVENANCE_TYPE,
                "--source-ref",
                "refs/heads/main",
                "--deny-self-hosted-runners",
            ]
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RegistryError("provider attestation verifier") from None
    if completed.returncode != 0:
        raise RegistryError("provider attestation signature")


class HttpRegistryProvider:
    """Small stdlib GitHub/GHCR read-only provider for the CLI."""

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        attestation_verifier: AttestationVerifier | None = None,
    ) -> None:
        if timeout <= 0:
            raise RegistryError("provider timeout")
        if attestation_verifier is not None and not callable(attestation_verifier):
            raise RegistryError("provider attestation verifier")
        self._token = token
        self._timeout = timeout
        self._attestation_verifier = (
            verify_attestation_with_gh
            if attestation_verifier is None
            else attestation_verifier
        )

    def _request(
        self,
        url: str,
        *,
        accept: str = "application/json",
        bearer_token: str | None = None,
        include_github_token: bool = True,
    ) -> tuple[bytes, Mapping[str, str]]:
        _validated_https_url(url, _github_redirect_host, "provider url")
        headers = {
            "Accept": accept,
            "User-Agent": "better-semantic-release-registry-loader",
        }
        request = urllib.request.Request(  # noqa: S310 - URL validated above.
            url, headers=headers, method="GET"
        )
        authorization = (
            bearer_token
            if bearer_token is not None
            else self._token
            if include_github_token
            else None
        )
        if authorization:
            request.add_unredirected_header("Authorization", f"Bearer {authorization}")
        return _open_bounded(
            urllib.request.build_opener(_SafeRedirectHandler()),
            request,
            self._timeout,
            http_error="provider http",
            transport_error="provider transport",
        )

    def _json(self, url: str, *, include_github_token: bool = True) -> Any:
        data, _headers = self._request(
            url,
            include_github_token=include_github_token,
        )
        try:
            return json.loads(
                data.decode("utf-8"),
                object_pairs_hook=_pairs_without_duplicates,
                parse_constant=_reject_constant,
            )
        except _DuplicateKey:
            raise RegistryError("provider duplicate key") from None
        except (UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
            raise RegistryError("provider json") from None

    def list_releases(
        self, repository: str, *, page: int, per_page: int
    ) -> list[Mapping[str, Any]]:
        url = f"https://api.github.com/repos/{urllib.parse.quote(repository, safe='/')}/releases?per_page={per_page}&page={page}"
        value = self._json(url)
        if not isinstance(value, list):
            raise RegistryError("provider releases")
        return cast(list[Mapping[str, Any]], value)

    def list_release_assets(
        self, repository: str, release_id: int, *, page: int, per_page: int
    ) -> list[Mapping[str, Any]]:
        url = f"https://api.github.com/repos/{urllib.parse.quote(repository, safe='/')}/releases/{release_id}/assets?per_page={per_page}&page={page}"
        value = self._json(url)
        if not isinstance(value, list):
            raise RegistryError("provider assets")
        return cast(list[Mapping[str, Any]], value)

    def download_asset(self, url: str) -> bytes:
        _validated_https_url(url, _github_asset_host, "provider asset url")
        data, _headers = self._request(url, accept="application/octet-stream")
        return data

    def peel_tag(self, repository: str, tag: str) -> str:
        safe_repo = urllib.parse.quote(repository, safe="/")
        safe_tag = urllib.parse.quote(tag, safe="")
        value = self._json(
            f"https://api.github.com/repos/{safe_repo}/git/ref/tags/{safe_tag}"
        )
        for _depth in range(8):
            if not isinstance(value, Mapping):
                raise RegistryError("provider tag")
            obj = value.get("object")
            if not isinstance(obj, Mapping):
                raise RegistryError("provider tag")
            sha = obj.get("sha")
            kind = obj.get("type")
            if not isinstance(sha, str) or not isinstance(kind, str):
                raise RegistryError("provider tag")
            if kind == "commit":
                return sha
            if kind != "tag":
                raise RegistryError("provider tag")
            value = self._json(
                f"https://api.github.com/repos/{safe_repo}/git/tags/{urllib.parse.quote(sha, safe='')}"
            )
        raise RegistryError("provider tag depth")

    def read_blob(self, repository: str, path: str, sha: str) -> Mapping[str, Any]:
        safe_repo = urllib.parse.quote(repository, safe="/")
        safe_path = urllib.parse.quote(path, safe="/")
        value = self._json(
            f"https://api.github.com/repos/{safe_repo}/contents/{safe_path}?ref={urllib.parse.quote(sha, safe='')}"
        )
        if not isinstance(value, Mapping):
            raise RegistryError("provider blob")
        return value

    def read_oci_manifest(self, image_ref: str, image_digest: str) -> str:
        prefix = "ghcr.io/"
        if not image_ref.startswith(prefix):
            raise RegistryError("provider image ref")
        path = image_ref[len(prefix) :]
        if _GHCR_PUBLISHER_PATH_RE.fullmatch(path) is None:
            raise RegistryError("provider image ref")
        token_query = urllib.parse.urlencode(
            {
                "service": "ghcr.io",
                "scope": f"repository:{path}:pull",
            }
        )
        token_response = self._json(
            f"https://ghcr.io/token?{token_query}",
            include_github_token=False,
        )
        if not isinstance(token_response, Mapping):
            raise RegistryError("provider registry token")
        registry_token = _text(
            token_response.get("token"),
            "provider registry token",
            max_length=16384,
        )
        _data, headers = self._request(
            f"https://ghcr.io/v2/{urllib.parse.quote(path, safe='/')}/manifests/{urllib.parse.quote(image_digest, safe=':')}",
            accept="application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
            bearer_token=registry_token,
            include_github_token=False,
        )
        digest = headers.get("docker-content-digest")
        if digest is None:
            raise RegistryError("provider manifest digest")
        return digest

    def _request_public_bundle(self, url: str) -> tuple[bytes, Mapping[str, str]]:
        _validated_https_url(
            url,
            lambda host: host == "tmaproduction.blob.core.windows.net",
            "provider attestation url",
        )
        request = urllib.request.Request(  # noqa: S310 - URL validated above.
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "better-semantic-release-registry-loader",
            },
            method="GET",
        )
        return _open_bounded(
            urllib.request.build_opener(_NoRedirectHandler()),
            request,
            self._timeout,
            http_error="provider attestation http",
            transport_error="provider attestation transport",
        )

    def read_attestation(
        self, repository: str, subject_digest: str, attestation_id: str
    ) -> str:
        repository = _repository(repository, "repository")
        subject_digest = _image_digest(subject_digest, "subject digest")
        attestation_id = _validate_attestation_id(attestation_id)
        safe_repo = urllib.parse.quote(repository, safe="/")
        safe_digest = urllib.parse.quote(subject_digest, safe="")
        response = self._json(
            f"https://api.github.com/repos/{safe_repo}/attestations/{safe_digest}"
        )
        bundle_url = _select_attestation_bundle_url(response, attestation_id)
        raw, headers = self._request_public_bundle(bundle_url)
        bundle_bytes = _attestation_json_bytes(raw, headers)
        bundle = _parse_attestation_json(bundle_bytes)
        statement = _validate_attestation_bundle(bundle, subject_digest)
        try:
            verified = self._attestation_verifier(
                bundle_bytes,
                repository,
                subject_digest,
            )
        except RegistryError:
            raise
        except Exception:  # noqa: BLE001 - verifier failures are normalized.
            raise RegistryError("provider attestation signature") from None
        if verified is not None and verified is not True:
            raise RegistryError("provider attestation signature")
        return hashlib.sha256(_canonical_json(statement)).hexdigest()


def verify_with_cosign(
    payload: bytes,
    bundle: bytes,
    *,
    identity: str,
    cosign_binary: str = "cosign",
    timeout: float = 30.0,
) -> None:
    """Verify one bundle; all cosign policy is isolated at this boundary."""
    if not isinstance(identity, str) or not identity or timeout <= 0:
        raise RegistryError("signature verifier")
    try:
        with tempfile.TemporaryDirectory(prefix="bsr-registry-") as directory:
            payload_path = os.path.join(directory, "record.json")
            bundle_path = os.path.join(directory, "bundle.json")
            with open(payload_path, "wb") as payload_file:
                payload_file.write(_bytes_value(payload, "registry asset"))
            with open(bundle_path, "wb") as bundle_file:
                bundle_file.write(_bytes_value(bundle, "signature bundle"))
            command = [
                cosign_binary,
                "verify-blob",
                "--bundle",
                bundle_path,
                "--certificate-identity",
                identity,
                "--certificate-oidc-issuer",
                _COSIGN_ISSUER,
                payload_path,
            ]
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
    except (OSError, subprocess.SubprocessError, ValueError):
        raise RegistryError("signature verifier") from None
    if completed.returncode != 0:
        raise RegistryError("signature verification")


def _read_input(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read(_MAX_RECORD_BYTES + 1)
    try:
        with open(path, "rb") as stream:
            return stream.read(_MAX_RECORD_BYTES + 1)
    except OSError:
        raise RegistryError("input read") from None


def _write_output(path: str, data: bytes) -> None:
    if path == "-":
        sys.stdout.buffer.write(data)
        return
    try:
        with open(path, "wb") as stream:
            stream.write(data)
    except OSError:
        raise RegistryError("output write") from None


def _cli_time(value: str | None) -> _datetime.datetime | None:
    if value is None:
        return None
    return _timestamp(value, "now")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="action_pin_registry")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="build a canonical registry record")
    generate.add_argument("--input", default="-", help="JSON source, or - for stdin")
    generate.add_argument(
        "--output", default="-", help="canonical output, or - for stdout"
    )
    generate.add_argument(
        "--previous",
        help="verified previous record, or canonical JSON null for an initial record",
    )

    verify = commands.add_parser("verify", help="verify canonical registry bytes")
    verify.add_argument("--input", default="-", help="record source, or - for stdin")
    verify.add_argument(
        "--output", default="-", help="canonical output, or - for stdout"
    )
    verify.add_argument("--now", help="verification time in UTC YYYY-MM-DDTHH:MM:SSZ")

    head = commands.add_parser("head", help="read the remote signed chain head")
    head.add_argument("--repository", required=True)
    head.add_argument("--identity", required=True)
    head.add_argument("--output", default="-", help="canonical output, or - for stdout")
    head.add_argument("--token", default=None)
    head.add_argument("--api-timeout", type=float, default=30.0)
    head.add_argument("--cosign", default="cosign")
    head.add_argument("--cosign-timeout", type=float, default=30.0)
    head.add_argument("--now", help="verification time in UTC YYYY-MM-DDTHH:MM:SSZ")

    load = commands.add_parser("load", help="read and verify a remote registry")
    load.add_argument("--expected", required=True, help="expected tuple JSON")
    load.add_argument("--repository", required=True)
    load.add_argument("--token", default=None)
    load.add_argument("--api-timeout", type=float, default=30.0)
    load.add_argument("--cosign", default="cosign")
    load.add_argument("--cosign-timeout", type=float, default=30.0)
    load.add_argument("--now", help="verification time in UTC YYYY-MM-DDTHH:MM:SSZ")
    return parser


def _run_cli(arguments: argparse.Namespace) -> None:
    if arguments.command == "generate":
        source = _parse_json(_read_input(arguments.input))
        if arguments.previous is None:
            record = build_record(source)
        else:
            previous_raw = _read_input(arguments.previous)
            previous = (
                None if previous_raw.strip() == b"null" else verify_record(previous_raw)
            )
            record = build_chained_record(source, previous)
        _write_output(arguments.output, canonical_record_bytes(record))
        return
    if arguments.command == "verify":
        raw = _read_input(arguments.input)
        checked = verify_record(raw, now=_cli_time(arguments.now))
        _write_output(arguments.output, canonical_record_bytes(checked))
        return
    token = (
        arguments.token
        if arguments.token is not None
        else os.environ.get("GITHUB_TOKEN")
    )
    provider = HttpRegistryProvider(token=token, timeout=arguments.api_timeout)
    if arguments.command == "head":
        identity = _text(arguments.identity, "signature identity", max_length=4096)
        head = load_remote_chain_head(
            provider=provider,
            signature_verifier=lambda payload, bundle: verify_with_cosign(
                payload,
                bundle,
                identity=identity,
                cosign_binary=arguments.cosign,
                timeout=arguments.cosign_timeout,
            ),
            repository=arguments.repository,
            now=_cli_time(arguments.now),
        )
        encoded = b"null\n" if head is None else canonical_record_bytes(head)
        _write_output(arguments.output, encoded)
        return
    expected = verify_record(
        _read_input(arguments.expected),
        now=_cli_time(arguments.now),
    )

    publisher = _mapping(expected["publisher"], _PUBLISHER_KEYS, "publisher")
    identity = cast(str, publisher["identity"])
    output = load_remote_registry(
        expected=expected,
        provider=provider,
        signature_verifier=lambda payload, bundle: verify_with_cosign(
            payload,
            bundle,
            identity=identity,
            cosign_binary=arguments.cosign,
            timeout=arguments.cosign_timeout,
        ),
        repository=arguments.repository,
        now=_cli_time(arguments.now),
    )
    _write_output("-", _canonical_json(output) + b"\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one offline generation/verification or one read-only remote load."""
    arguments = _parser().parse_args(argv)
    try:
        _run_cli(arguments)
    except RegistryError:
        sys.stderr.write("registry error\n")
        return 2
    return 0


__all__ = [
    "AttestationVerifier",
    "HttpRegistryProvider",
    "RegistryError",
    "RegistryProvider",
    "SignatureVerifier",
    "build_record",
    "canonical_record_bytes",
    "load_remote_registry",
    "main",
    "select_latest",
    "verify_attestation_with_gh",
    "verify_record",
    "verify_with_cosign",
]


if __name__ == "__main__":
    raise SystemExit(main())
