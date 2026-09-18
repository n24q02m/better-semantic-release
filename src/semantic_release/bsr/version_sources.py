"""
Universal version *sources* (Task 4): adapters that read the current version
from wherever a project keeps it, each reporting provenance.

Bridge-first: upstream ``version_toml``/``version_variables`` declarations and
git tags keep working through bridge adapters; native JSON/TOML/text backends
cover the rest of the common ecosystems. Backends deferred by the plan
(YAML, XML, command) are deliberately absent -- unknown ``kind`` values fail
closed at config-parse time.

Every source answers two questions:

- ``load()`` -- the version(s) currently visible at this location;
- ``provenance()`` -- where that value came from (kind + location + detail),
  so ``doctor`` can name the exact file/field when sources drift apart.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import tomlkit
from dotty_dict import Dotty
from git import Repo

from semantic_release.version.declarations.enum import VersionStampType
from semantic_release.version.declarations.toml import TomlVersionDeclaration
from semantic_release.version.version import Version

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence

    from semantic_release.version.declarations.i_version_replacer import (
        IVersionReplacer,
    )
    from semantic_release.version.translator import VersionTranslator

# The #10 group inside is the semver core; prerelease/build metadata are
# optional so Version.parse can decide PEP440-vs-semver semantics itself.
_VERSION_TOKEN_RE = r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?"  # noqa: S105 - version regex, not a secret


def build_version_pattern_regex(pattern: str) -> re.Pattern[str]:
    """
    Compile a text pattern with a literal ``{version}`` token into a regex.

    ``split`` removes the delimiter, so the version group is re-inserted
    between consecutive pieces rather than matched by part equality.
    """
    pieces = pattern.split("{version}")
    if len(pieces) < 2:
        raise ValueError(
            f"pattern {pattern!r} must contain the literal {{version}} token"
        )
    parts: list[str] = []
    for index, piece in enumerate(pieces):
        parts.append(re.escape(piece))
        if index < len(pieces) - 1:
            parts.append(f"(?P<version>{_VERSION_TOKEN_RE})")
    return re.compile("".join(parts))


@runtime_checkable
class VersionSource(Protocol):
    """Read-side adapter contract: load versions, report where they came from."""

    def load(self) -> set[Version]:
        """Return the version(s) currently visible at this source."""
        ...

    def provenance(self) -> VersionProvenance:
        """Describe where the value came from (kind + location + detail)."""
        ...


@dataclass(frozen=True)
class VersionProvenance:
    """Where a version value came from, for drift reporting."""

    kind: str  # "git-tag" | "toml" | "json" | "text"
    location: str  # file path or "git tags"
    detail: str = ""  # field path / text pattern


class GitTagVersionSource:
    """Versions recoverable from the repository's git tags."""

    def __init__(self, repo_dir: Path | str, translator: VersionTranslator) -> None:
        self._repo_dir = Path(repo_dir)
        self._translator = translator

    def load(self) -> set[Version]:
        from semantic_release.version.algorithm import tags_and_versions

        with Repo(str(self._repo_dir)) as git_repo:
            return {
                version
                for _, version in tags_and_versions(git_repo.tags, self._translator)
            }

    def provenance(self) -> VersionProvenance:
        return VersionProvenance(kind="git-tag", location="git tags")


class TomlVersionSource:
    """Version at a dotted key inside a TOML file (native read, no deprecated parse)."""

    def __init__(self, path: Path | str, field: str) -> None:
        self._path = Path(path)
        self._field = field

    def load(self) -> set[Version]:
        if not self._path.exists():
            return set()
        doc = tomlkit.loads(self._path.read_text(encoding="utf-8"))
        dotty = Dotty(doc)
        if self._field not in dotty:
            return set()
        raw = dotty[self._field]
        if not isinstance(raw, str) or not raw.strip():
            return set()
        version = Version.parse(raw.strip())
        return {version} if version else set()

    def provenance(self) -> VersionProvenance:
        return VersionProvenance(
            kind="toml", location=str(self._path), detail=self._field
        )


class JsonVersionSource:
    """Version at a dotted key inside a JSON file (e.g. ``package.json``)."""

    def __init__(self, path: Path | str, field: str) -> None:
        self._path = Path(path)
        self._field = field

    def load(self) -> set[Version]:
        if not self._path.exists():
            return set()
        doc = json.loads(self._path.read_text(encoding="utf-8"))
        raw: object = doc
        for part in self._field.split("."):
            if not isinstance(raw, dict) or part not in raw:
                return set()
            raw = raw[part]
        if not isinstance(raw, str) or not raw.strip():
            return set()
        version = Version.parse(raw.strip())
        return {version} if version else set()

    def provenance(self) -> VersionProvenance:
        return VersionProvenance(
            kind="json", location=str(self._path), detail=self._field
        )


class TextVersionSource:
    """
    Version matched by a regex pattern in a plain text file.

    The configured pattern contains a literal ``{version}`` token marking
    where the version sits, e.g. ``version = "{version}"`` or ``{version}``.
    """

    def __init__(self, path: Path | str, pattern: str) -> None:
        self._path = Path(path)
        self._pattern = pattern

    def _regex(self) -> re.Pattern[str]:
        return build_version_pattern_regex(self._pattern)

    def load(self) -> set[Version]:
        if not self._path.exists():
            return set()
        found: set[Version] = set()
        for match in self._regex().finditer(self._path.read_text(encoding="utf-8")):
            version = Version.parse(match.group("version"))
            if version:
                found.add(version)
        return found

    def provenance(self) -> VersionProvenance:
        return VersionProvenance(
            kind="text", location=str(self._path), detail=self._pattern
        )


class DeclarationVersionSource:
    """Bridge: reads through an upstream :class:`IVersionReplacer` declaration."""

    def __init__(self, declaration: IVersionReplacer, label: str) -> None:
        self._declaration = declaration
        self._label = label

    def load(self) -> set[Version]:
        try:
            return self._declaration.parse()
        except NotImplementedError:
            # Declaration subclasses without a concrete read path (e.g. the
            # FileVersionDeclaration base) report no version; drift findings
            # surface it as "no version found" instead of crashing doctor.
            return set()

    def provenance(self) -> VersionProvenance:
        return VersionProvenance(kind="declaration", location=self._label)


def _declaration_label(declaration: IVersionReplacer) -> str:
    path = getattr(declaration, "_path", None) or getattr(
        declaration, "_filepath", None
    )
    if path is not None:
        return str(path)
    return type(declaration).__name__


def resolve_version_sources(
    *,
    repo_dir: Path | str,
    translator: VersionTranslator,
    declarations: Sequence[IVersionReplacer] = (),
    source_configs: Sequence[object] = (),
) -> list[VersionSource]:
    """
    Build the source list: explicit ``[tool.bsr.version]`` sources, else the bridge default.

    With no explicit sources the bridge default is: git tags plus every
    upstream version declaration -- the same view ``doctor`` needs for drift
    reporting.
    """
    sources: list[VersionSource] = []
    if source_configs:
        from semantic_release.bsr.config import BsrVersionSourceConfig

        for config in source_configs:
            if not isinstance(config, BsrVersionSourceConfig):  # pragma: no cover
                raise TypeError(f"unsupported source config: {type(config).__name__}")
            if config.kind == "git-tag":
                sources.append(GitTagVersionSource(repo_dir, translator))
            elif config.kind == "toml":
                sources.append(
                    TomlVersionSource(
                        config.path
                        if Path(config.path).is_absolute()
                        else Path(repo_dir) / config.path,
                        config.field,
                    )
                )
            elif config.kind == "json":
                sources.append(
                    JsonVersionSource(
                        config.path
                        if Path(config.path).is_absolute()
                        else Path(repo_dir) / config.path,
                        config.field,
                    )
                )
            elif config.kind == "text":
                sources.append(
                    TextVersionSource(
                        config.path
                        if Path(config.path).is_absolute()
                        else Path(repo_dir) / config.path,
                        config.pattern,
                    )
                )
        return sources

    sources.append(GitTagVersionSource(repo_dir, translator))
    sources.extend(
        DeclarationVersionSource(declaration, _declaration_label(declaration))
        for declaration in declarations
    )
    return sources


__all__ = [
    "DeclarationVersionSource",
    "GitTagVersionSource",
    "JsonVersionSource",
    "TextVersionSource",
    "TomlVersionSource",
    "VersionProvenance",
    "VersionSource",
    "VersionStampType",
    "TomlVersionDeclaration",
    "resolve_version_sources",
]
