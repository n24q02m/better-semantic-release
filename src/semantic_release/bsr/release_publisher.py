"""Strict, resumable GitHub release publisher for the BSR G1 action.

The module deliberately has no project dependencies. ``reconcile`` consumes a
small provider protocol so manifest validation and reconciliation can be tested
without network access. The HTTP provider is kept at the boundary and never
puts credentials or response bodies into exceptions or logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os

import re
import socket
import ssl
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast


MANIFEST_SCHEMA = "bsr-release-manifest/v1"
_API_VERSION = "2022-11-28"
_API_HOST = "api.github.com"
_UPLOAD_HOST = "uploads.github.com"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_OBJECT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_TRANSACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DESCRIPTOR_STAGING = bool(
    os.name != "nt"
    and getattr(os, "O_DIRECTORY", 0)
    and getattr(os, "O_NOFOLLOW", 0)
    and hasattr(os, "supports_dir_fd")
    and os.open in os.supports_dir_fd
)


def _before_workspace_component_open(
    workspace: Path, parts: tuple[str, ...], scope: str
) -> None:
    """Test seam invoked immediately before each descriptor-anchored open."""
    del workspace, parts, scope
_TOP_KEYS = (
    "schema_version",
    "repository",
    "transaction_id",
    "tag",
    "target_sha",
    "release",
    "assets",
)
_RELEASE_KEYS = (
    "name",
    "body_path",
    "body_sha256",
    "draft",
    "prerelease",
    "make_latest",
)
_ASSET_KEYS = ("name", "path", "size", "sha256", "content_type")
_RESULT_KEYS = (
    "transaction_id",
    "manifest_sha256",
    "release_id",
    "release_url",
    "created",
    "uploaded_count",
    "reused_count",
    "asset_set_sha256",
    "result_sha256",
)


class PublisherError(Exception):
    """Base class whose message is intentionally value-safe."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"publisher {code}")


class ManifestError(PublisherError):
    """The manifest or a declared local input is invalid."""


class ProviderError(PublisherError):
    """A provider read or write failed safely."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(code)


class AmbiguousProviderError(ProviderError):
    """A write may have reached the provider and requires readback."""


class ProviderTransportError(AmbiguousProviderError):
    """A provider transport timeout or connection failure."""


class ProviderHttpError(ProviderError):
    """A bounded provider response returned an HTTP error status."""

    def __init__(self, code: str, *, status: int) -> None:
        super().__init__(code, status=status)


@dataclass(frozen=True)
class PublishLimits:
    """Bounds applied before and during provider interaction."""

    max_manifest_bytes: int = 1_048_576
    max_file_bytes: int = 256 * 1024 * 1024
    max_response_bytes: int = 4 * 1024 * 1024
    max_pages: int = 100
    asset_page_size: int = 100
    max_tag_depth: int = 32
    max_redirects: int = 3
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.max_manifest_bytes,
                self.max_file_bytes,
                self.max_response_bytes,
                self.max_pages,
                self.asset_page_size,
                self.max_tag_depth,
                self.max_redirects,
            )
        ):
            raise ValueError("publisher limits must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("publisher timeout must be positive")


@dataclass(frozen=True)
class AssetSpec:
    name: str
    path: str
    size: int
    sha256: str
    content_type: str

    @property
    def digest(self) -> str:
        return self.sha256


@dataclass(frozen=True)
class ReleaseSpec:
    name: str
    body_path: str
    body_sha256: str
    draft: bool
    prerelease: bool
    make_latest: bool


@dataclass(frozen=True)
class ReleaseManifest:
    schema_version: str
    repository: str
    transaction_id: str
    tag: str
    target_sha: str
    release: ReleaseSpec
    assets: tuple[AssetSpec, ...]
    raw_sha256: str
    asset_set_sha256: str


@dataclass(frozen=True)
class StagedFile:
    path: Path
    size: int
    sha256: str

    def read_bytes(self) -> bytes:
        """Read the action-owned copy, never the caller-owned source again."""
        try:
            with self.path.open("rb") as source:
                data = source.read()
        except OSError:
            raise ManifestError("staged file read") from None
        if len(data) != self.size or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ManifestError("staged file changed")
        return data


@dataclass
class PreparedManifest:
    manifest: ReleaseManifest
    body: StagedFile
    assets: Mapping[str, StagedFile]
    _temporary_directory: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def __enter__(self) -> PreparedManifest:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class GithubProvider(Protocol):
    """Minimal injected boundary used by the reconciliation state machine."""

    def get_tag_ref(self, repository: str, tag: str) -> Mapping[str, Any]: ...

    def get_tag_object(self, repository: str, sha: str) -> Mapping[str, Any]: ...

    def get_release_by_tag(
        self, repository: str, tag: str
    ) -> Mapping[str, Any] | None: ...

    def create_release(
        self, repository: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def list_release_assets(
        self, repository: str, release_id: int, *, page: int, per_page: int
    ) -> Sequence[Mapping[str, Any]]: ...

    def upload_asset(
        self,
        repository: str,
        release_id: int,
        name: str,
        content_type: str,
        data: bytes,
    ) -> Mapping[str, Any]: ...

    def get_latest_release(self, repository: str) -> Mapping[str, Any] | None: ...


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
            raise ManifestError("manifest control character")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ManifestError("manifest key")
            _walk_for_controls(key)
            _walk_for_controls(child)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for child in value:
            _walk_for_controls(child)


def _parse_manifest(raw: bytes) -> Mapping[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError("manifest encoding") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        raise ManifestError("manifest duplicate key") from None
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ManifestError("manifest json") from None
    if not isinstance(value, Mapping):
        raise ManifestError("manifest object")
    _walk_for_controls(value)
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: tuple[str, ...], scope: str) -> None:
    actual = tuple(value.keys())
    if set(actual) != set(expected):
        raise ManifestError(f"manifest {scope} key")
    if actual != expected:
        raise ManifestError(f"manifest {scope} key order")


def _require_string(value: Any, scope: str, *, max_length: int = 1024) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ManifestError(f"manifest {scope}")
    if any(ord(char) < 0x20 for char in value):
        raise ManifestError(f"manifest {scope}")
    return value


def _validate_repository(value: Any, expected: str | None) -> str:
    repository = _require_string(value, "repository", max_length=200)
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise ManifestError("manifest repository")
    if expected is not None and repository != expected:
        raise ManifestError("manifest repository mismatch")
    return repository


def _validate_transaction(value: Any) -> str:
    transaction_id = _require_string(value, "transaction_id", max_length=128)
    if _TRANSACTION_RE.fullmatch(transaction_id) is None:
        raise ManifestError("manifest transaction_id")
    return transaction_id


def _validate_tag(value: Any) -> str:
    tag = _require_string(value, "tag", max_length=255)
    if (
        tag.startswith("/")
        or tag.endswith("/")
        or tag.endswith(".")
        or ".." in tag
        or "@{" in tag
        or "\\" in tag
        or any(char in tag for char in "~^:?*[\"")
        or any(not part or part.startswith(".") for part in tag.split("/"))
    ):
        raise ManifestError("manifest tag")
    return tag


def _validate_target_sha(value: Any) -> str:
    if not isinstance(value, str) or _OBJECT_SHA_RE.fullmatch(value) is None:
        raise ManifestError("manifest target_sha")
    if value != value.lower():
        raise ManifestError("manifest target_sha")
    return value


def _validate_relative_path(value: Any, scope: str) -> str:
    path = _require_string(value, scope, max_length=4096)
    if (
        "\\" in path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path) is not None
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ManifestError(f"manifest {scope} path")
    return path


def _validate_body_sha(value: Any) -> str:
    digest = _require_string(value, "body_sha256", max_length=64)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ManifestError("manifest body_sha256")
    return digest


def _validate_asset_name(value: Any) -> str:
    name = _require_string(value, "asset name", max_length=255)
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ManifestError("manifest asset name")
    return name


def _validate_asset(value: Any) -> AssetSpec:
    if not isinstance(value, Mapping):
        raise ManifestError("manifest asset object")
    _require_exact_keys(value, _ASSET_KEYS, "asset")
    name = _validate_asset_name(value["name"])
    path = _validate_relative_path(value["path"], "asset")
    size = value["size"]
    if type(size) is not int or size < 0:
        raise ManifestError("manifest asset size")
    digest = _require_string(value["sha256"], "asset sha256", max_length=71)
    if not digest.startswith("sha256:") or _SHA256_RE.fullmatch(digest[7:]) is None:
        raise ManifestError("manifest asset sha256")
    content_type = _require_string(value["content_type"], "asset content_type", max_length=255)
    return AssetSpec(name, path, size, digest, content_type)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ManifestError("manifest canonical json") from None


def _assert_workspace_file(path: Path, workspace: Path, scope: str) -> Path:
    root = Path(os.path.abspath(os.fspath(workspace)))
    candidate = Path(os.path.abspath(os.fspath(path)))
    try:
        if os.path.commonpath((os.fspath(root), os.fspath(candidate))) != os.fspath(root):
            raise ManifestError(f"manifest {scope} path")
    except ValueError:
        raise ManifestError(f"manifest {scope} path") from None
    try:
        root_stat = os.lstat(root)
    except OSError:
        raise ManifestError("manifest workspace") from None
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise ManifestError("manifest workspace")
    relative = os.path.relpath(candidate, root)
    if relative == os.curdir or relative.startswith(os.pardir + os.sep):
        raise ManifestError(f"manifest {scope} path")
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            current_stat = os.lstat(current)
        except OSError:
            raise ManifestError(f"manifest {scope} file") from None
        if stat.S_ISLNK(current_stat.st_mode):
            raise ManifestError(f"manifest {scope} symlink")
    return candidate


def _workspace_relative_parts(
    path: Path, workspace: Path, scope: str
) -> tuple[str, ...]:
    root = Path(os.path.abspath(os.fspath(workspace)))
    candidate = Path(os.path.abspath(os.fspath(path)))
    try:
        if os.path.commonpath((os.fspath(root), os.fspath(candidate))) != os.fspath(root):
            raise ManifestError(f"manifest {scope} path")
    except ValueError:
        raise ManifestError(f"manifest {scope} path") from None
    relative = os.path.relpath(candidate, root)
    if relative == os.curdir or relative.startswith(os.pardir + os.sep):
        raise ManifestError(f"manifest {scope} path")
    parts = tuple(Path(relative).parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ManifestError(f"manifest {scope} path")
    return parts


def _open_descriptor_file(path: Path, workspace: Path, scope: str) -> int:
    parts = _workspace_relative_parts(path, workspace, scope)
    directory_fds: list[int] = []
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        _before_workspace_component_open(workspace, (), scope)
        root_fd = os.open(os.fspath(workspace), directory_flags)
        directory_fds.append(root_fd)
        for index, part in enumerate(parts[:-1]):
            prefix = parts[: index + 1]
            _before_workspace_component_open(workspace, prefix, scope)
            child_fd = os.open(part, directory_flags, dir_fd=directory_fds[-1])
            directory_fds.append(child_fd)
        _before_workspace_component_open(workspace, parts, scope)
        return os.open(parts[-1], file_flags, dir_fd=directory_fds[-1])
    except OSError:
        raise ManifestError(f"manifest {scope} file") from None
    finally:
        for directory_fd in reversed(directory_fds):
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _open_workspace_file(
    path: Path, workspace: Path, scope: str
) -> tuple[int, os.stat_result | None]:
    if _DESCRIPTOR_STAGING:
        return _open_descriptor_file(path, workspace, scope), None
    source = _assert_workspace_file(path, workspace, scope)
    try:
        before = os.lstat(source)
    except OSError:
        raise ManifestError(f"manifest {scope} file") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ManifestError(f"manifest {scope} file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        flags |= no_follow
    try:
        return os.open(os.fspath(source), flags), before
    except OSError:
        raise ManifestError(f"manifest {scope} file") from None


def _read_regular_once(
    path: Path, *, workspace: Path, limit: int, scope: str
) -> bytes:
    fd, before = _open_workspace_file(path, workspace, scope)
    try:
        with os.fdopen(fd, "rb") as source:
            after = os.fstat(source.fileno())
            if not stat.S_ISREG(after.st_mode):
                raise ManifestError(f"manifest {scope} file")
            if (before is not None and before.st_size > limit) or after.st_size > limit:
                raise ManifestError(f"manifest {scope} size")
            if before is not None:
                no_follow = getattr(os, "O_NOFOLLOW", 0)
                if no_follow == 0 and os.path.realpath(path) != os.path.abspath(path):
                    raise ManifestError(f"manifest {scope} symlink")
                if (
                    getattr(before, "st_ino", 0)
                    and getattr(after, "st_ino", 0)
                    and (
                        before.st_ino != after.st_ino
                        or before.st_dev != after.st_dev
                    )
                ):
                    raise ManifestError(f"manifest {scope} changed")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = source.read(min(1024 * 1024, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ManifestError(f"manifest {scope} size")
                chunks.append(chunk)
            return b"".join(chunks)
    except ManifestError:
        raise
    except OSError:
        raise ManifestError(f"manifest {scope} read") from None


def _stage_file(
    source_path: Path,
    destination: Path,
    *,
    workspace: Path,
    expected_size: int | None,
    expected_sha256: str,
    limits: PublishLimits,
    scope: str,
) -> StagedFile:
    fd, before = _open_workspace_file(source_path, workspace, scope)
    total = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as input_file, destination.open("wb") as output_file:
            after = os.fstat(input_file.fileno())
            if not stat.S_ISREG(after.st_mode):
                raise ManifestError(f"manifest {scope} file")
            if (before is not None and before.st_size > limits.max_file_bytes) or (
                after.st_size > limits.max_file_bytes
            ):
                raise ManifestError(f"manifest {scope} size")
            if expected_size is not None and (
                (before is not None and before.st_size != expected_size)
                or after.st_size != expected_size
            ):
                raise ManifestError(f"manifest {scope} size")
            if before is not None:
                no_follow = getattr(os, "O_NOFOLLOW", 0)
                if no_follow == 0 and os.path.realpath(source_path) != os.path.abspath(
                    source_path
                ):
                    raise ManifestError(f"manifest {scope} symlink")
                if (
                    getattr(before, "st_ino", 0)
                    and getattr(after, "st_ino", 0)
                    and (
                        before.st_ino != after.st_ino
                        or before.st_dev != after.st_dev
                    )
                ):
                    raise ManifestError(f"manifest {scope} changed")
            while True:
                chunk = input_file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_file_bytes or (
                    expected_size is not None and total > expected_size
                ):
                    raise ManifestError(f"manifest {scope} size")
                digest.update(chunk)
                output_file.write(chunk)
    except ManifestError:
        raise
    except OSError:
        raise ManifestError(f"manifest {scope} read") from None
    actual_digest = digest.hexdigest()
    if expected_size is not None and total != expected_size:
        raise ManifestError(f"manifest {scope} size")
    if actual_digest != expected_sha256:
        raise ManifestError(f"manifest {scope} digest")
    return StagedFile(destination, total, actual_digest)


def _manifest_path(path: Path | str, workspace: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    if _DESCRIPTOR_STAGING:
        _workspace_relative_parts(candidate, workspace, "manifest")
        return Path(os.path.abspath(os.fspath(candidate)))
    return _assert_workspace_file(candidate, workspace, "manifest")


def prepare_manifest(
    manifest_path: Path | str,
    workspace: Path | str,
    *,
    expected_repository: str | None = None,
    limits: PublishLimits | None = None,
) -> PreparedManifest:
    """Validate and stage all caller-owned files before provider construction."""
    bounds = limits or PublishLimits()
    root = Path(os.path.abspath(os.fspath(workspace)))
    manifest_file = _manifest_path(manifest_path, root)
    raw = _read_regular_once(
        manifest_file,
        workspace=root,
        limit=bounds.max_manifest_bytes,
        scope="manifest",
    )
    parsed = _parse_manifest(raw)
    _require_exact_keys(parsed, _TOP_KEYS, "top-level")
    if parsed["schema_version"] != MANIFEST_SCHEMA:
        raise ManifestError("manifest schema")
    repository = _validate_repository(parsed["repository"], expected_repository)
    transaction_id = _validate_transaction(parsed["transaction_id"])
    tag = _validate_tag(parsed["tag"])
    target_sha = _validate_target_sha(parsed["target_sha"])
    release_value = parsed["release"]
    if not isinstance(release_value, Mapping):
        raise ManifestError("manifest release object")
    _require_exact_keys(release_value, _RELEASE_KEYS, "release")
    release_name = _require_string(release_value["name"], "release name", max_length=255)
    body_path = _validate_relative_path(release_value["body_path"], "body")
    body_sha = _validate_body_sha(release_value["body_sha256"])
    draft = release_value["draft"]
    prerelease = release_value["prerelease"]
    if type(draft) is not bool or type(prerelease) is not bool:
        raise ManifestError("manifest release flags")
    make_latest = release_value["make_latest"]
    # GitHub's computed `legacy` policy has no stable exact readback. Only
    # explicit booleans are accepted so post-read can prove the policy.
    if type(make_latest) is not bool:
        raise ManifestError("manifest make_latest")
    if make_latest and (draft or prerelease):
        raise ManifestError("manifest make_latest")
    assets_value = parsed["assets"]
    if not isinstance(assets_value, list):
        raise ManifestError("manifest assets array")
    assets = tuple(_validate_asset(value) for value in assets_value)
    names = [asset.name for asset in assets]
    if names != sorted(names) or len(set(names)) != len(names):
        raise ManifestError("manifest asset order")
    paths = [body_path, *(asset.path for asset in assets)]
    if len(set(paths)) != len(paths):
        raise ManifestError("manifest duplicate path")
    release_manifest = ReleaseManifest(
        MANIFEST_SCHEMA,
        repository,
        transaction_id,
        tag,
        target_sha,
        ReleaseSpec(release_name, body_path, body_sha, draft, prerelease, make_latest),
        assets,
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(
            _canonical_json(
                [
                    {
                        "name": asset.name,
                        "path": asset.path,
                        "size": asset.size,
                        "sha256": asset.sha256,
                        "content_type": asset.content_type,
                    }
                    for asset in assets
                ]
            )
        ).hexdigest(),
    )
    temporary = tempfile.TemporaryDirectory(prefix="bsr-publisher-")
    temporary_path = Path(temporary.name)
    try:
        body = _stage_file(
            root / Path(*body_path.split("/")),
            temporary_path / "body",
            workspace=root,
            expected_size=None,
            expected_sha256=body_sha,
            limits=bounds,
            scope="body",
        )
        staged_assets: dict[str, StagedFile] = {}
        for index, asset in enumerate(assets):
            staged_assets[asset.name] = _stage_file(
                root / Path(*asset.path.split("/")),
                temporary_path / f"asset-{index}",
                workspace=root,
                expected_size=asset.size,
                expected_sha256=asset.sha256[7:],
                limits=bounds,
                scope="asset",
            )
    except Exception:
        temporary.cleanup()
        raise
    return PreparedManifest(release_manifest, body, staged_assets, temporary)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderError(code)
    return value


def _invoke(
    provider: GithubProvider,
    operation: str,
    method_name: str,
    *args: Any,
    ambiguous_write: bool = False,
    **kwargs: Any,
) -> Any:
    try:
        method = getattr(provider, method_name)
        return method(*args, **kwargs)
    except PublisherError:
        raise
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
        if ambiguous_write:
            raise ProviderTransportError(operation) from None
        raise ProviderError(operation) from None
    except Exception:
        if ambiguous_write:
            raise AmbiguousProviderError(operation) from None
        raise ProviderError(operation) from None


def _is_ambiguous(exc: BaseException) -> bool:
    if isinstance(exc, (AmbiguousProviderError, ProviderTransportError)):
        return True
    if isinstance(exc, ProviderHttpError):
        return exc.status in {408, 409, 422} or exc.status >= 500
    return isinstance(exc, (TimeoutError, socket.timeout, urllib.error.URLError, OSError))


def _object_info(value: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    nested = value.get("object")
    if isinstance(nested, Mapping):
        return nested
    if "type" in value and "sha" in value:
        return value
    raise ProviderError(operation)


def peel_tag(
    provider: GithubProvider,
    repository: str,
    tag: str,
    *,
    max_depth: int = 32,
) -> str:
    """Peel lightweight or recursively annotated tags with cycle protection."""
    if max_depth <= 0:
        raise ProviderError("tag depth")
    ref = _mapping(_invoke(provider, "tag ref", "get_tag_ref", repository, tag), "tag ref")
    current = _object_info(ref, "tag ref")
    seen: set[str] = set()
    for _depth in range(max_depth):
        object_type = current.get("type")
        object_sha = current.get("sha")
        if not isinstance(object_type, str) or not isinstance(object_sha, str):
            raise ProviderError("tag response")
        if _OBJECT_SHA_RE.fullmatch(object_sha) is None:
            raise ProviderError("tag response")
        normalized_sha = object_sha.lower()
        if object_type == "commit":
            return normalized_sha
        if object_type != "tag" or normalized_sha in seen:
            raise ProviderError("tag cycle")
        seen.add(normalized_sha)
        response = _mapping(
            _invoke(provider, "tag object", "get_tag_object", repository, normalized_sha),
            "tag object",
        )
        current = _object_info(response, "tag object")
    raise ProviderError("tag depth")


def _release_id(value: Mapping[str, Any]) -> int:
    release_id = value.get("id")
    if type(release_id) is not int or release_id <= 0:
        raise ProviderError("release id")
    return release_id


def _release_url(value: Mapping[str, Any]) -> str:
    url = value.get("html_url")
    if not isinstance(url, str) or not url or any(ord(char) < 0x20 for char in url):
        raise ProviderError("release url")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderError("release url")
    return url


def _release_matches(value: Mapping[str, Any], manifest: ReleaseManifest, body: str) -> bool:
    release = manifest.release
    # GitHub accepts make_latest on create but does not return it in the
    # release object. The dedicated /releases/latest read below is the
    # authoritative proof of the explicit boolean policy.
    return (
        value.get("tag_name") == manifest.tag
        and value.get("name") == release.name
        and value.get("body") == body
        and type(value.get("draft")) is bool
        and value.get("draft") is release.draft
        and type(value.get("prerelease")) is bool
        and value.get("prerelease") is release.prerelease
    )


def _assert_release(value: Any, manifest: ReleaseManifest, body: str) -> tuple[int, str]:
    release = _mapping(value, "release response")
    if not _release_matches(release, manifest, body):
        raise ProviderError("release metadata")
    return _release_id(release), _release_url(release)


def _asset_from_provider(value: Any) -> tuple[str, int, str]:
    asset = _mapping(value, "asset response")
    name = asset.get("name")
    size = asset.get("size")
    digest = asset.get("digest")
    if (
        not isinstance(name, str)
        or not name
        or any(ord(char) < 0x20 for char in name)
        or type(size) is not int
        or size < 0
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or _SHA256_RE.fullmatch(digest[7:]) is None
    ):
        raise ProviderError("asset response")
    return name, size, digest


def _read_assets(
    provider: GithubProvider,
    repository: str,
    release_id: int,
    limits: PublishLimits,
) -> dict[str, tuple[int, str]]:
    assets: dict[str, tuple[int, str]] = {}
    for page in range(1, limits.max_pages + 1):
        page_value = _invoke(
            provider,
            "list assets",
            "list_release_assets",
            repository,
            release_id,
            page=page,
            per_page=limits.asset_page_size,
        )
        if isinstance(page_value, (str, bytes, bytearray)) or not isinstance(
            page_value, Sequence
        ):
            raise ProviderError("asset page")
        page_items = list(page_value)
        for item in page_items:
            name, size, digest = _asset_from_provider(item)
            if name in assets:
                raise ProviderError("duplicate asset")
            assets[name] = (size, digest)
        if len(page_items) < limits.asset_page_size:
            return assets
    raise ProviderError("asset pagination")


def _assert_asset_set(
    current: Mapping[str, tuple[int, str]], assets: Sequence[AssetSpec]
) -> None:
    expected = {asset.name: (asset.size, asset.sha256) for asset in assets}
    foreign = set(current) - set(expected)
    if foreign:
        raise ProviderError("foreign asset")
    for name, actual in current.items():
        if actual != expected[name]:
            raise ProviderError("asset digest or size")


def _latest_policy(
    provider: GithubProvider,
    repository: str,
    manifest: ReleaseManifest,
) -> None:
    latest_value = _invoke(
        provider, "latest release", "get_latest_release", repository
    )
    if latest_value is None:
        if manifest.release.make_latest:
            raise ProviderError("latest policy")
        return
    latest = _mapping(latest_value, "latest response")
    latest_tag = latest.get("tag_name")
    if not isinstance(latest_tag, str):
        raise ProviderError("latest policy")
    if manifest.release.make_latest != (latest_tag == manifest.tag):
        raise ProviderError("latest policy")


def _create_release(
    provider: GithubProvider,
    repository: str,
    manifest: ReleaseManifest,
    body: str,
) -> tuple[Mapping[str, Any], bool]:
    payload = {
        "tag_name": manifest.tag,
        "name": manifest.release.name,
        "body": body,
        "draft": manifest.release.draft,
        "prerelease": manifest.release.prerelease,
        "make_latest": "true" if manifest.release.make_latest else "false",
    }
    try:
        created = _invoke(
            provider,
            "create release",
            "create_release",
            repository,
            payload,
            ambiguous_write=True,
        )
    except PublisherError as exc:
        if not _is_ambiguous(exc):
            raise
    else:
        try:
            _assert_release(created, manifest, body)
        except ProviderError:
            pass
        else:
            return _mapping(created, "release response"), True
    readback = _invoke(
        provider, "release readback", "get_release_by_tag", repository, manifest.tag
    )
    if readback is None:
        raise AmbiguousProviderError("create release readback")
    _assert_release(readback, manifest, body)
    return _mapping(readback, "release response"), True


def _upload_one(
    provider: GithubProvider,
    repository: str,
    release_id: int,
    asset: AssetSpec,
    data: bytes,
    current: dict[str, tuple[int, str]],
    expected_assets: Sequence[AssetSpec],
    limits: PublishLimits,
) -> bool:
    try:
        response = _invoke(
            provider,
            "upload asset",
            "upload_asset",
            repository,
            release_id,
            asset.name,
            asset.content_type,
            data,
            ambiguous_write=True,
        )
        try:
            name, size, digest = _asset_from_provider(response)
        except ProviderError:
            raise AmbiguousProviderError("upload response") from None
        if (name, size, digest) != (asset.name, asset.size, asset.sha256):
            raise AmbiguousProviderError("upload response")
        current[name] = (size, digest)
        return True
    except PublisherError as exc:
        if not _is_ambiguous(exc):
            raise
    # Never replay an ambiguous write. Relisting is the only convergence path.
    reread = _read_assets(provider, repository, release_id, limits)
    _assert_asset_set(reread, expected_assets)
    exact = reread.get(asset.name)
    if exact != (asset.size, asset.sha256):
        raise AmbiguousProviderError("upload readback")
    current.clear()
    current.update(reread)
    return True


def _result_document(
    manifest: ReleaseManifest,
    *,
    release_id: int,
    release_url: str,
    created: bool,
    uploaded_count: int,
    reused_count: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "transaction_id": manifest.transaction_id,
        "manifest_sha256": manifest.raw_sha256,
        "release_id": release_id,
        "release_url": release_url,
        "created": created,
        "uploaded_count": uploaded_count,
        "reused_count": reused_count,
        "asset_set_sha256": manifest.asset_set_sha256,
    }
    result["result_sha256"] = hashlib.sha256(_canonical_json(result)).hexdigest()
    return result


def reconcile(
    prepared: PreparedManifest,
    provider: GithubProvider,
    *,
    limits: PublishLimits | None = None,
) -> dict[str, Any]:
    """Execute the read-before-write, no-overwrite reconciliation machine."""
    bounds = limits or PublishLimits()
    manifest = prepared.manifest
    try:
        body = prepared.body.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ManifestError("body encoding") from None
    if "\x00" in body:
        raise ManifestError("body control character")
    peeled = peel_tag(
        provider,
        manifest.repository,
        manifest.tag,
        max_depth=bounds.max_tag_depth,
    )
    if peeled != manifest.target_sha:
        raise ProviderError("tag target")
    existing = _invoke(
        provider,
        "release read",
        "get_release_by_tag",
        manifest.repository,
        manifest.tag,
    )
    created = False
    if existing is None:
        existing, created = _create_release(
            provider, manifest.repository, manifest, body
        )
    release_id, release_url = _assert_release(existing, manifest, body)
    current = _read_assets(provider, manifest.repository, release_id, bounds)
    _assert_asset_set(current, manifest.assets)
    reused_count = len(current)
    uploaded_count = 0
    for asset in manifest.assets:
        if asset.name in current:
            continue
        uploaded = _upload_one(
            provider,
            manifest.repository,
            release_id,
            asset,
            prepared.assets[asset.name].read_bytes(),
            current,
            manifest.assets,
            bounds,
        )
        if uploaded:
            uploaded_count += 1
    # Complete post-read: tag, exact release metadata/id, every paginated asset,
    # and the explicit latest policy are all checked again after writes.
    final_peeled = peel_tag(
        provider,
        manifest.repository,
        manifest.tag,
        max_depth=bounds.max_tag_depth,
    )
    if final_peeled != manifest.target_sha:
        raise ProviderError("tag target")
    final_release_value = _invoke(
        provider,
        "release post-read",
        "get_release_by_tag",
        manifest.repository,
        manifest.tag,
    )
    if final_release_value is None:
        raise ProviderError("release post-read")
    final_release = _mapping(final_release_value, "release post-read")
    final_id, final_url = _assert_release(final_release, manifest, body)
    if final_id != release_id:
        raise ProviderError("release identity")
    final_assets = _read_assets(provider, manifest.repository, final_id, bounds)
    _assert_asset_set(final_assets, manifest.assets)
    if set(final_assets) != {asset.name for asset in manifest.assets}:
        raise ProviderError("asset set")
    _latest_policy(provider, manifest.repository, manifest)
    return _result_document(
        manifest,
        release_id=final_id,
        release_url=final_url,
        created=created,
        uploaded_count=uploaded_count,
        reused_count=reused_count,
    )


class _OutputChannel:
    """A verified GitHub output descriptor retained across provider writes."""

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def write(self, values: Mapping[str, Any]) -> None:
        fd = self._fd
        if fd is None:
            raise ProviderError("output write")
        try:
            with os.fdopen(os.dup(fd), "a", encoding="utf-8", newline="\n") as output:
                for key in _RESULT_KEYS:
                    output.write(f"{key}={values[key]}\n")
        except OSError:
            raise ProviderError("output write") from None

    def close(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass


def _open_output_channel() -> _OutputChannel | None:
    output_path_value = os.environ.get("GITHUB_OUTPUT")
    if not output_path_value:
        return None
    output_path = Path(output_path_value)
    fd: int | None = None
    try:
        before = os.lstat(output_path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ProviderError("output preflight")
        flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if no_follow:
            flags |= no_follow
        fd = os.open(os.fspath(output_path), flags)
        after = os.fstat(fd)
        if not stat.S_ISREG(after.st_mode):
            raise ProviderError("output preflight")
        if (
            getattr(before, "st_ino", 0)
            and getattr(after, "st_ino", 0)
            and (
                before.st_ino != after.st_ino
                or before.st_dev != after.st_dev
            )
        ):
            raise ProviderError("output preflight")
        channel = _OutputChannel(fd)
        fd = None
        return channel
    except ProviderError:
        raise
    except OSError:
        raise ProviderError("output preflight") from None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _preflight_output() -> None:
    channel = _open_output_channel()
    if channel is not None:
        channel.close()


def publish_manifest(
    manifest_path: Path | str,
    *,
    workspace: Path | str,
    repository: str | None = None,
    provider: GithubProvider | None = None,
    provider_factory: Callable[[str], GithubProvider] | None = None,
    token: str | None = None,
    limits: PublishLimits | None = None,
    output_channel: _OutputChannel | None = None,
) -> dict[str, Any]:
    """Prepare local inputs first, then construct/use the provider exactly once."""
    bounds = limits or PublishLimits()
    owns_output_channel = output_channel is None
    with prepare_manifest(
        manifest_path,
        workspace,
        expected_repository=repository,
        limits=bounds,
    ) as prepared:
        if owns_output_channel:
            output_channel = _open_output_channel()
        try:
            if provider is None:
                if provider_factory is None:
                    provider_factory = lambda value: HttpGithubProvider(value, limits=bounds)
                if not isinstance(token, str) or not token:
                    raise ProviderError("token input")
                try:
                    provider = provider_factory(token)
                except PublisherError:
                    raise
                except Exception:
                    raise ProviderError("provider init") from None
            return reconcile(prepared, provider, limits=bounds)
        finally:
            if owns_output_channel and output_channel is not None:
                output_channel.close()


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, max_redirects: int) -> None:
        super().__init__()
        self.max_redirects = max_redirects
        self.redirects = 0

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        self.redirects += 1
        if self.redirects > self.max_redirects:
            raise ProviderError("redirect bound")
        _validate_github_url(newurl, allow_upload=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_github_url(url: str, *, allow_upload: bool) -> None:
    parsed = urllib.parse.urlsplit(url)
    hosts = {_API_HOST}
    if allow_upload:
        hosts.add(_UPLOAD_HOST)
    if parsed.scheme != "https" or parsed.hostname not in hosts:
        raise ProviderError("provider url")
    if parsed.username is not None or parsed.password is not None:
        raise ProviderError("provider url")


_CA_BUNDLE_PATH = "/etc/ssl/certs/ca-certificates.crt"


def _github_ssl_context() -> ssl.SSLContext:
    try:
        if os.name == "nt":
            context = ssl.create_default_context()
        else:
            bundle = os.lstat(_CA_BUNDLE_PATH)
            if (
                stat.S_ISLNK(bundle.st_mode)
                or not stat.S_ISREG(bundle.st_mode)
                or getattr(bundle, "st_uid", -1) != 0
                or bundle.st_mode & 0o022
            ):
                raise ProviderError("provider ca")
            context = ssl.create_default_context(cafile=_CA_BUNDLE_PATH)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context
    except ProviderError:
        raise
    except (OSError, ssl.SSLError, ValueError):
        raise ProviderError("provider ca") from None


def _read_bounded(response: Any, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ProviderError("response size")
        chunks.append(chunk)
    return b"".join(chunks)


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if not callable(close):
        return
    try:
        close()
    except (OSError, ValueError):
        pass


def _parse_response(data: bytes) -> Any:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
        )
    except (_DuplicateKey, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        raise ProviderError("response json") from None
    return value


class HttpGithubProvider:
    """Bounded urllib implementation for the GitHub REST API."""

    def __init__(
        self,
        token: str,
        *,
        limits: PublishLimits | None = None,
    ) -> None:
        if (
            not isinstance(token, str)
            or not token
            or any(ord(char) < 0x20 for char in token)
        ):
            raise ProviderError("token input")
        self._token = token
        self._limits = limits or PublishLimits()

    def _request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        expected_status: set[int],
        not_found: bool = False,
        upload: bool = False,
        ambiguous_write: bool = False,
    ) -> Any:
        base = f"https://{_UPLOAD_HOST if upload else _API_HOST}"
        url = base + path
        _validate_github_url(url, allow_upload=upload)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "better-semantic-release-publisher/1",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=_github_ssl_context()),
            _SafeRedirectHandler(self._limits.max_redirects),
        )
        response: Any | None = None
        data: bytes | None = None
        dispatch_started = False
        try:
            dispatch_started = True
            response = opener.open(request, timeout=self._limits.timeout_seconds)
            status_value = getattr(response, "status", None)
            status = int(
                status_value if status_value is not None else response.getcode()
            )
            if status not in expected_status:
                try:
                    _read_bounded(response, self._limits.max_response_bytes)
                except ProviderError:
                    if ambiguous_write:
                        raise AmbiguousProviderError(
                            operation, status=status
                        ) from None
                    raise
                if ambiguous_write:
                    raise AmbiguousProviderError(operation, status=status)
                raise ProviderHttpError(operation, status=status)
            try:
                data = _read_bounded(response, self._limits.max_response_bytes)
            except ProviderError:
                if ambiguous_write:
                    raise AmbiguousProviderError(operation, status=status) from None
                raise
        except urllib.error.HTTPError as exc:
            response = exc
            try:
                _read_bounded(exc, self._limits.max_response_bytes)
            except ProviderError:
                pass
            if ambiguous_write:
                raise AmbiguousProviderError(operation, status=exc.code) from None
            if not_found and exc.code == 404:
                return None
            if exc.code in {409, 422}:
                raise AmbiguousProviderError(operation, status=exc.code) from None
            raise ProviderHttpError(operation, status=exc.code) from None
        except ProviderError as exc:
            if ambiguous_write and dispatch_started and not isinstance(
                exc, AmbiguousProviderError
            ):
                raise AmbiguousProviderError(
                    operation, status=getattr(exc, "status", None)
                ) from None
            raise
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
            raise ProviderTransportError(operation) from None
        finally:
            if response is not None:
                _close_response(response)
        if data is None:
            raise ProviderError("response body")
        try:
            return _parse_response(data)
        except ProviderError:
            if ambiguous_write:
                raise AmbiguousProviderError(operation) from None
            raise

    @staticmethod
    def _repo_path(repository: str) -> str:
        if _REPOSITORY_RE.fullmatch(repository) is None:
            raise ProviderError("repository")
        return "/repos/" + urllib.parse.quote(repository, safe="/")

    def get_tag_ref(self, repository: str, tag: str) -> Mapping[str, Any]:
        path = self._repo_path(repository) + "/git/ref/tags/" + urllib.parse.quote(tag, safe="")
        value = self._request("tag ref", "GET", path, expected_status={200})
        return cast(Mapping[str, Any], value)

    def get_tag_object(self, repository: str, sha: str) -> Mapping[str, Any]:
        path = self._repo_path(repository) + "/git/tags/" + urllib.parse.quote(sha, safe="")
        value = self._request("tag object", "GET", path, expected_status={200})
        return cast(Mapping[str, Any], value)

    def get_release_by_tag(
        self, repository: str, tag: str
    ) -> Mapping[str, Any] | None:
        path = self._repo_path(repository) + "/releases/tags/" + urllib.parse.quote(tag, safe="")
        value = self._request(
            "release read", "GET", path, expected_status={200}, not_found=True
        )
        return cast(Mapping[str, Any] | None, value)

    def create_release(
        self, repository: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        path = self._repo_path(repository) + "/releases"
        value = self._request(
            "create release",
            "POST",
            path,
            body=_canonical_json(dict(payload)),
            content_type="application/json",
            expected_status={201},
            ambiguous_write=True,
        )
        return cast(Mapping[str, Any], value)

    def list_release_assets(
        self, repository: str, release_id: int, *, page: int, per_page: int
    ) -> Sequence[Mapping[str, Any]]:
        if page <= 0 or per_page <= 0:
            raise ProviderError("asset pagination")
        path = (
            self._repo_path(repository)
            + f"/releases/{release_id}/assets?page={page}&per_page={per_page}"
        )
        value = self._request("list assets", "GET", path, expected_status={200})
        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
            raise ProviderError("asset page")
        return cast(Sequence[Mapping[str, Any]], value)

    def upload_asset(
        self,
        repository: str,
        release_id: int,
        name: str,
        content_type: str,
        data: bytes,
    ) -> Mapping[str, Any]:
        path = (
            self._repo_path(repository)
            + f"/releases/{release_id}/assets?name={urllib.parse.quote(name, safe='')}"
        )
        value = self._request(
            "upload asset",
            "POST",
            path,
            body=data,
            content_type=content_type,
            expected_status={201},
            ambiguous_write=True,
        )
        return cast(Mapping[str, Any], value)

    def get_latest_release(self, repository: str) -> Mapping[str, Any] | None:
        path = self._repo_path(repository) + "/releases/latest"
        value = self._request(
            "latest release", "GET", path, expected_status={200}, not_found=True
        )
        return cast(Mapping[str, Any] | None, value)


def _write_outputs(
    values: Mapping[str, Any], output_channel: _OutputChannel | None = None
) -> None:
    owns_output_channel = output_channel is None
    if owns_output_channel:
        output_channel = _open_output_channel()
    if output_channel is None:
        return
    try:
        output_channel.write(values)
    finally:
        if owns_output_channel:
            output_channel.close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry used by the eventual Docker action."""
    parser = argparse.ArgumentParser(prog="bsr-publisher")
    parser.add_argument("--manifest", default=os.environ.get("INPUT_MANIFEST", ""))
    args = parser.parse_args(argv)
    workspace = os.environ.get("GITHUB_WORKSPACE", os.getcwd())
    repository = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("INPUT_TOKEN", "")
    output_channel: _OutputChannel | None = None
    try:
        if not args.manifest:
            raise ManifestError("manifest input")
        if not repository:
            raise ManifestError("repository input")
        output_channel = _open_output_channel()
        result = publish_manifest(
            args.manifest,
            workspace=workspace,
            repository=repository,
            token=token,
            output_channel=output_channel,
        )
        _write_outputs(result, output_channel)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except PublisherError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("publisher failed", file=sys.stderr)
        return 1
    finally:
        if output_channel is not None:
            output_channel.close()


if __name__ == "__main__":
    raise SystemExit(main())
