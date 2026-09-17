"""
Universal version *targets* (Task 4): adapters that write a new version to
wherever a project keeps it.

Each target answers the same four questions:

- ``preview(new)`` -- a human-readable description of what would change;
- ``apply(new, noop)`` -- perform (or dry-run) the write, returning the
  touched path or ``None``;
- ``touched_files()`` -- the paths this target may modify;
- ``validate_consistency(new)`` -- ``None`` when the target is in a sane
  state for ``new``, otherwise an actionable mismatch message.

Backends the plan defers (YAML, XML, command) are deliberately absent;
unknown ``kind`` values fail closed at config-parse time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from git import Repo

from semantic_release.version.declarations.enum import VersionStampType
from semantic_release.version.declarations.toml import TomlVersionDeclaration

if TYPE_CHECKING:  # pragma: no cover
    from semantic_release.version.declarations.i_version_replacer import (
        IVersionReplacer,
    )
    from semantic_release.version.translator import VersionTranslator
    from semantic_release.version.version import Version


class VersionTarget(Protocol):
    """Write-side adapter contract: preview, apply, report, validate."""

    def preview(self, new_version: Version) -> str: ...

    def apply(
        self, new_version: Version, noop: bool = False
    ) -> Path | None:
        """Write the version (or report what would happen); return the touched path."""
        ...

    def touched_files(self) -> list[Path]: ...

    def validate_consistency(self, new_version: Version) -> str | None:
        """Return an actionable message when the target is unfit for ``new_version``."""
        ...


class GitTagVersionTarget:
    """Writes the release as a git tag rendered through the configured tag format."""

    def __init__(self, repo_dir: Path | str, translator: VersionTranslator) -> None:
        self._repo_dir = Path(repo_dir)
        self._translator = translator

    def _tag_name(self, version: Version) -> str:
        return self._translator.str_to_tag(str(version))

    def preview(self, new_version: Version) -> str:
        return f"would create git tag {self._tag_name(new_version)!r} at HEAD"

    def apply(self, new_version: Version, noop: bool = False) -> Path | None:
        tag_name = self._tag_name(new_version)
        if noop:
            return None
        with Repo(str(self._repo_dir)) as git_repo:
            if tag_name in (tag.name for tag in git_repo.tags):
                raise ValueError(
                    f"git tag {tag_name!r} already exists; refusing to re-cut a tag"
                )
            git_repo.create_tag(tag_name)
        return None

    def touched_files(self) -> list[Path]:
        return []

    def validate_consistency(self, new_version: Version) -> str | None:
        tag_name = self._tag_name(new_version)
        with Repo(str(self._repo_dir)) as git_repo:
            if tag_name not in (tag.name for tag in git_repo.tags):
                return None
            head = git_repo.head.commit
            tagged = git_repo.tags[tag_name].commit
            if tagged == head:
                return None
            return (
                f"git tag {tag_name!r} already exists on a different commit "
                f"({tagged.hexsha[:12]} != HEAD {head.hexsha[:12]})"
            )


class TomlVersionTarget:
    """Bridge over the upstream :class:`TomlVersionDeclaration` write path."""

    def __init__(self, path: Path | str, field: str, repo_dir: Path | str) -> None:
        self._repo_dir = Path(repo_dir)
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = self._repo_dir / resolved
        self._path = resolved
        self._declaration = TomlVersionDeclaration(
            self._path, field, VersionStampType.NUMBER_FORMAT
        )
        self._field = field

    def preview(self, new_version: Version) -> str:
        return f"would set {self._path}:{self._field} = {new_version}"

    def apply(self, new_version: Version, noop: bool = False) -> Path | None:
        return self._declaration.update_file_w_version(new_version, noop=noop)

    def touched_files(self) -> list[Path]:
        return [self._path]

    def validate_consistency(self, new_version: Version) -> str | None:
        del new_version  # existence check is version-independent
        if not self._path.exists():
            return f"TOML target file {self._path} does not exist"
        return None


def _detect_indent(raw: str) -> str:
    match = re.search(r"\n([ \t]+)\S", raw)
    return match.group(1) if match else "  "


class JsonVersionTarget:
    """Version at a dotted key inside a JSON file, preserving indentation."""

    def __init__(self, path: Path | str, field: str, repo_dir: Path | str) -> None:
        self._repo_dir = Path(repo_dir)
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = self._repo_dir / resolved
        self._path = resolved
        self._field = field

    def preview(self, new_version: Version) -> str:
        return f"would set {self._path}:{self._field} = {new_version}"

    def _write(self, new_version: Version) -> None:
        raw = self._path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        parent: dict[str, object] = doc
        parts = self._field.split(".")
        for part in parts[:-1]:
            node = parent.get(part)
            if not isinstance(node, dict):
                raise ValueError(  # noqa: TRY004 - invalid config shape, not a type misuse
                    f"{self._path}: cannot descend into {part!r} for field "
                    f"{self._field!r}: parent is not an object"
                )
            parent = node
        parent[parts[-1]] = str(new_version)
        indent = _detect_indent(raw)
        trailing = "\n" if raw.endswith("\n") else ""
        self._path.write_text(json.dumps(doc, indent=indent) + trailing, encoding="utf-8")

    def apply(self, new_version: Version, noop: bool = False) -> Path | None:
        if noop:
            del new_version  # preview surface only; nothing to write
            if not self._path.exists():
                return None
            return self._path
        self._write(new_version)
        return self._path

    def touched_files(self) -> list[Path]:
        return [self._path]

    def validate_consistency(self, new_version: Version) -> str | None:
        del new_version  # existence check is version-independent
        if not self._path.exists():
            return f"JSON target file {self._path} does not exist"
        return None


class TextVersionTarget:
    """
    Version matched by a ``{version}`` token pattern in a plain text file.

    A pattern that does not match the file fails closed -- a silent no-op
    write here would mean the release ships while the file stays stale.
    """

    def __init__(self, path: Path | str, pattern: str, repo_dir: Path | str) -> None:
        self._repo_dir = Path(repo_dir)
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = self._repo_dir / resolved
        self._path = resolved
        self._pattern = pattern

    def _regex(self) -> re.Pattern[str]:
        from semantic_release.bsr.version_sources import build_version_pattern_regex

        return build_version_pattern_regex(self._pattern)

    def preview(self, new_version: Version) -> str:
        return f"would stamp {self._path} with {new_version} (pattern {self._pattern!r})"

    def apply(self, new_version: Version, noop: bool = False) -> Path | None:
        if not self._path.exists():
            if noop:
                return None
            raise FileNotFoundError(f"path {str(self._path)!r} does not exist")
        raw = self._path.read_text(encoding="utf-8")
        regex = self._regex()
        if not regex.search(raw):
            if noop:
                return None
            raise ValueError(
                f"{self._path}: pattern {self._pattern!r} matched nothing; "
                "refusing to leave the file stale"
            )
        if noop:
            return self._path

        def _swap(match: re.Match[str]) -> str:
            group_start, group_end = match.span("version")
            text = match.group(0)
            offset = group_start - match.start()
            return text[:offset] + str(new_version) + text[offset + (group_end - group_start) :]

        updated = regex.sub(_swap, raw)
        if updated != raw:
            self._path.write_text(updated, encoding="utf-8")
            return self._path
        return None

    def touched_files(self) -> list[Path]:
        return [self._path]

    def validate_consistency(self, new_version: Version) -> str | None:
        del new_version  # existence check is version-independent
        if not self._path.exists():
            return f"text target file {self._path} does not exist"
        return None


class DeclarationVersionTarget:
    """Bridge over an upstream :class:`IVersionReplacer` declaration."""

    def __init__(self, declaration: IVersionReplacer) -> None:
        self._declaration = declaration

    def preview(self, new_version: Version) -> str:
        return f"would update {type(self._declaration).__name__} to {new_version}"


    def apply(self, new_version: Version, noop: bool = False) -> Path | None:
        return self._declaration.update_file_w_version(new_version, noop=noop)

    def touched_files(self) -> list[Path]:
        path = getattr(self._declaration, "_path", None) or getattr(
            self._declaration, "_filepath", None
        )
        return [Path(path)] if path is not None else []

    def validate_consistency(self, new_version: Version) -> str | None:
        del new_version  # bridge target has no local preconditions
        return None


def resolve_version_targets(
    *,
    repo_dir: Path | str,
    translator: VersionTranslator,
    declarations: list[IVersionReplacer] | None = None,
    target_configs: list[object] | None = None,
) -> list[VersionTarget]:
    """Build the target list: explicit ``[tool.bsr.version]`` targets, else the bridge default."""
    from semantic_release.bsr.config import BsrVersionTargetConfig

    targets: list[VersionTarget] = []
    if target_configs:
        for config in target_configs:
            if not isinstance(config, BsrVersionTargetConfig):  # pragma: no cover
                raise TypeError(f"unsupported target config: {type(config).__name__}")
            if config.kind == "git-tag":
                targets.append(GitTagVersionTarget(repo_dir, translator))
            elif config.kind == "toml":
                targets.append(
                    TomlVersionTarget(config.path, config.field, repo_dir)
                )
            elif config.kind == "json":
                targets.append(
                    JsonVersionTarget(config.path, config.field, repo_dir)
                )
            elif config.kind == "text":
                targets.append(
                    TextVersionTarget(config.path, config.pattern, repo_dir)
                )
        return targets

    if declarations is not None:
        targets.extend(DeclarationVersionTarget(declaration) for declaration in declarations)
    return targets


__all__ = [
    "DeclarationVersionTarget",
    "GitTagVersionTarget",
    "JsonVersionTarget",
    "TextVersionTarget",
    "TomlVersionTarget",
    "VersionTarget",
    "resolve_version_targets",
]
