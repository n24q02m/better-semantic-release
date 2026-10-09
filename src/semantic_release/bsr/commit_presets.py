r"""
BSR-PATCH: commit-message preset packs for the verify leg (W2.3).

Validates/normalizes commit *subjects* against a named convention so `verify`
can enforce (or just observe) commit hygiene for the pending release range.
This module is read-only: it never rewrites history, amends commits, or
touches the index.

Built-in presets::

    conventional   default; strict Conventional Commits subjects
                   (also registered as the `commitizen` alias)
    gitmoji-map    a leading gitmoji (unicode or :shortcode:) normalized to a
                   conventional type via the shipped map

Custom packs are TOML files::

    name = "my-pack"  # required
    mode = "warn"  # optional: parse | warn | reject (default reject)
    pattern = "^(?P<type>\\\\w+)!\\\\s"  # required regex, matched at subject start
    [types]  # optional captured-type -> conventional type
    feat = "feature"  #   (left side = canonical type, right = raw alias)

Modes::

    parse    validate and report; never contributes a failing row
    warn     non-conforming commits surface as warning-severity rows
    reject   non-conforming commits surface as blocker rows (verify fails)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping

try:  # pragma: no cover - Python >=3.11 always has tomllib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.8-3.10 only
    import tomlkit

    tomllib = None  # type: ignore[assignment]

PresetMode = Literal["parse", "warn", "reject"]

PRESET_MODES: tuple[PresetMode, ...] = ("parse", "warn", "reject")

_NONASCII_RE = re.compile(r"[^\x00-\x7F]")

DEFAULT_PRESET = "conventional"

# Conventional Commits v1.0.0 subject line:
#   type[(scope)][!]: description   -- description must be non-empty.
_CONVENTIONAL_PATTERN = r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]+)\))?(?P<breaking>!)?:\s(?P<description>\S.*)$"

# Types the gitmoji convention maps onto conventional semantics. Any gitmoji
# token not listed here still parses (the convention is the leading emoji),
# it just normalizes to "misc" instead of a conventional type.
_GITMOJI_TYPE_MAP: dict[str, str] = {
    # unicode emoji -> conventional type
    "\U0001f3a8": "style",  # 🎨
    "⚡": "perf",  # ⚡
    "\U0001f525": "remove",  # 🔥
    "\U0001f41b": "fix",  # 🐛
    "🚑": "fix",  # 🚑 ambulance
    "✨": "feat",  # ✨
    "\U0001f4dd": "docs",  # 📝
    "\U0001f680": "deploy",  # 🚀
    "\U0001f484": "style",  # 💄
    "\U0001f389": "init",  # 🎉
    "✅": "test",  # ✅
    "\U0001f512": "security",  # 🔒
    "\U0001f510": "security",  # 🔐
    "⬆️": "deps",  # ⬆️
    "⬇️": "deps",  # ⬇️
    "\U0001f4cc": "deps",  # 📌
    "➕": "deps",  # noqa: RUF001 - gitmoji glyphs are the convention itself
    "➖": "deps",  # noqa: RUF001, RUF003 - gitmoji glyphs are the convention itself
    "\U0001f527": "build",  # 🔧
    "\U0001f6a7": "build",  # 🚧
    "💚": "ci",  # 💚
    "👷": "ci",  # 👷
    "♻️": "refactor",  # ♻️
    "⏪": "revert",  # ⏪
    "\U0001f9ea": "test",  # 🧪
    "⚗️": "test",  # ⚗️
    "\U0001f3d7️": "build",  # 🏗️
    "\U0001f4a9": "misc",  # 💩
    "\U0001f3b7": "misc",  # 🎷
    "\U0001f9b9": "misc",  # 🦺
}

# gitmoji shortcode (:code:) -> conventional type. Names per gitmoji.dev.
_GITMOJI_CODE_MAP: dict[str, str] = {
    "art": "style",
    "zap": "perf",
    "fire": "remove",
    "bug": "fix",
    "ambulance": "fix",
    "sparkles": "feat",
    "memo": "docs",
    "pencil": "docs",
    "rocket": "deploy",
    "lipstick": "style",
    "tada": "init",
    "white_check_mark": "test",
    "lock": "security",
    "closed_lock_with_key": "security",
    "arrow_up": "deps",
    "arrow_down": "deps",
    "pushpin": "deps",
    "heavy_plus_sign": "deps",
    "heavy_minus_sign": "deps",
    "wrench": "build",
    "hammer": "build",
    "construction": "build",
    "green_heart": "ci",
    "construction_worker": "ci",
    "poop": "misc",
    "saxophone": "misc",
    "building_construction": "build",
    "rewind": "revert",
    "test_tube": "test",
    "alembic": "test",
    "safety_vest": "misc",
}

_GITMOJI_CODE_RE = re.compile(r"^:([a-z_]+):")


class CommitPresetError(Exception):
    """Raised when a preset spec or pack file cannot be resolved."""


@dataclass(frozen=True)
class ParsedSubject:
    """Result of matching one commit subject against a preset."""

    subject: str
    conforming: bool
    type: str = ""  # normalized conventional type ("" when unparseable/misc)
    raw_type: str = ""  # the type as captured, before normalization
    scope: str = ""
    breaking: bool = False


@dataclass(frozen=True)
class CommitPreset:
    """A named convention: how subjects match and what a violation means."""

    name: str
    mode: PresetMode
    matcher: Callable[[str], ParsedSubject]
    description: str = ""
    types_map: Mapping[str, str] = field(default_factory=dict)  # raw -> canonical


@dataclass(frozen=True)
class SubjectViolation:
    """One non-conforming commit in the audited range."""

    sha: str
    subject: str


@dataclass(frozen=True)
class CommitAudit:
    """Aggregate result of validating a commit range against a preset."""

    preset_name: str
    mode: PresetMode
    total: int
    violations: tuple[SubjectViolation, ...]
    types: Mapping[str, int] = field(default_factory=dict)

    @property
    def passed_count(self) -> int:
        return self.total - len(self.violations)


def _conventional_matcher(
    pattern: re.Pattern[str],
    types_map: Mapping[str, str],
) -> Callable[[str], ParsedSubject]:
    def match(subject: str) -> ParsedSubject:
        m = pattern.match(subject.strip())
        if not m:
            return ParsedSubject(subject=subject, conforming=False)
        groups = m.groupdict()
        raw_type = groups.get("type") or ""
        canonical = types_map.get(raw_type.lower(), raw_type.lower()) or raw_type
        return ParsedSubject(
            subject=subject,
            conforming=True,
            type=canonical.lower(),
            raw_type=raw_type,
            scope=groups.get("scope") or "",
            breaking=bool(groups.get("breaking")),
        )

    return match


def _gitmoji_matcher(subject: str) -> ParsedSubject:
    """Parse a leading gitmoji token (unicode glyph or :shortcode:)."""
    stripped = subject.strip()
    code = _GITMOJI_CODE_RE.match(stripped)
    if code:
        raw = code.group(1)
        return ParsedSubject(
            subject=subject,
            conforming=True,
            type=_GITMOJI_CODE_MAP.get(raw, "misc"),
            raw_type=f":{raw}:",
        )
    # Unicode gitmoji: first token must be a non-ascii glyph cluster, so an
    # accented word like "naïve" never counts as an emoji.
    token = stripped.split(" ", 1)[0] if " " in stripped else ""
    if (
        token
        and _NONASCII_RE.search(token) is not None
        and not any(ch.isascii() and ch.isalnum() for ch in token)
    ):
        raw = token.rstrip("️")  # variation selector
        return ParsedSubject(
            subject=subject,
            conforming=True,
            type=_GITMOJI_TYPE_MAP.get(raw, _GITMOJI_TYPE_MAP.get(token, "misc")),
            raw_type=token,
        )
    return ParsedSubject(subject=subject, conforming=False)


def _builtin_presets() -> dict[str, CommitPreset]:
    conventional = CommitPreset(
        name="conventional",
        mode="reject",
        matcher=_conventional_matcher(re.compile(_CONVENTIONAL_PATTERN), {}),
        description=(
            "Conventional Commits v1.0.0 subject: type[(scope)][!]: description"
        ),
    )
    gitmoji = CommitPreset(
        name="gitmoji-map",
        mode="reject",
        matcher=_gitmoji_matcher,
        description=(
            "gitmoji.dev convention: subject starts with a gitmoji (unicode or "
            ":shortcode:); mapped emojis normalize to conventional types"
        ),
        types_map=dict(_GITMOJI_CODE_MAP),
    )
    return {
        "conventional": conventional,
        "commitizen": CommitPreset(
            name="commitizen",
            mode="reject",
            matcher=conventional.matcher,
            description=(
                "Commitizen-compatible conventional subjects "
                "(same grammar as `conventional`)"
            ),
        ),
        "gitmoji-map": gitmoji,
    }


def _load_toml(path: Path) -> Mapping[str, object]:
    raw = path.read_text(encoding="utf-8")
    if tomllib is not None:
        return tomllib.loads(raw)
    return tomlkit.parse(raw)  # type: ignore[union-attr]


def _preset_from_toml(path: Path, mode_override: PresetMode | None) -> CommitPreset:
    try:
        doc = _load_toml(path)
    except Exception as exc:  # noqa: BLE001 - any parse error is a config error
        raise CommitPresetError(f"{path}: invalid TOML preset pack: {exc}") from exc
    if not isinstance(doc, Mapping):
        raise CommitPresetError(f"{path}: preset pack must be a TOML table")

    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CommitPresetError(f"{path}: preset pack requires a non-empty `name`")

    raw_mode = doc.get("mode", "reject")
    if raw_mode not in PRESET_MODES:
        raise CommitPresetError(
            f"{path}: `mode` must be one of {PRESET_MODES}, got {raw_mode!r}"
        )
    mode: PresetMode = mode_override or raw_mode

    pattern_src = doc.get("pattern")
    if not isinstance(pattern_src, str) or not pattern_src:
        raise CommitPresetError(f"{path}: preset pack requires a `pattern` regex")
    try:
        pattern = re.compile(pattern_src)
    except re.error as exc:
        raise CommitPresetError(f"{path}: `pattern` does not compile: {exc}") from exc

    raw_types = doc.get("types", {})
    if not isinstance(raw_types, Mapping):
        raise CommitPresetError(f"{path}: `types` must be a table")
    types_map = {
        str(raw).lower(): str(canonical)
        for canonical, raw in raw_types.items()
        if isinstance(raw, str) and isinstance(canonical, str)
    }

    return CommitPreset(
        name=name.strip(),
        mode=mode,
        matcher=_conventional_matcher(pattern, types_map),
        description=f"custom TOML preset pack loaded from {path.name}",
        types_map=types_map,
    )


def load_preset(spec: str) -> CommitPreset:
    """
    Resolve a preset spec to a CommitPreset.

    Spec grammar: ``<builtin-name>[:<mode>]`` or ``<path-to.toml>[:<mode>]``
    where ``mode`` is one of ``parse|warn|reject`` and overrides the mode the
    pack declares. A bare ``.toml`` path (or any non-builtin token containing
    a path separator or ending in ``.toml``) loads a custom pack.
    """
    token, _, suffix = spec.rpartition(":")
    mode_override: PresetMode | None = None
    base = spec
    if suffix in PRESET_MODES and token:
        mode_override = suffix
        base = token

    builtin = _builtin_presets().get(base)
    if builtin is not None:
        if mode_override is None:
            return builtin
        return CommitPreset(
            name=builtin.name,
            mode=mode_override,
            matcher=builtin.matcher,
            description=builtin.description,
            types_map=builtin.types_map,
        )

    path = Path(base)
    if not path.is_file():
        known = ", ".join(sorted(_builtin_presets()))
        raise CommitPresetError(
            f"unknown commit preset {base!r}: expected one of {known} "
            "or a path to a TOML preset pack"
        )
    return _preset_from_toml(path, mode_override)


def parse_subject(preset: CommitPreset, subject: str) -> ParsedSubject:
    """Match one commit subject against the preset."""
    return preset.matcher(subject)


def validate_subjects(
    preset: CommitPreset,
    subjects: Iterable[tuple[str, str]],
) -> CommitAudit:
    """
    Validate ``(sha, subject)`` pairs; pure, no repo access.

    Returns a CommitAudit with every non-conforming commit in ``violations``
    and a normalized type histogram for conforming commits.
    """
    total = 0
    violations: list[SubjectViolation] = []
    types: dict[str, int] = {}
    for sha, subject in subjects:
        total += 1
        parsed = preset.matcher(subject)
        if parsed.conforming:
            if parsed.type:
                types[parsed.type] = types.get(parsed.type, 0) + 1
        else:
            violations.append(SubjectViolation(sha=sha, subject=subject))
    return CommitAudit(
        preset_name=preset.name,
        mode=preset.mode,
        total=total,
        violations=tuple(violations),
        types=dict(sorted(types.items())),
    )


def commit_subjects_since_latest_tag(
    repo_dir: str | Path,
    *,
    max_count: int = 200,
) -> tuple[list[tuple[str, str]], str]:
    """
    Collect ``(sha, subject)`` for commits after the latest reachable tag.

    Merge commits (more than one parent) are skipped: upstream merge subjects
    are not conventional and rewriting them is out of scope for a validator.
    Returns the commits plus the base description ("tag vX.Y.Z" or
    "repository root").
    """
    from git import Repo  # deferred: gitpython is a runtime dep, keep import cheap
    from git.exc import GitCommandError

    with Repo(str(repo_dir)) as repo:
        try:
            base = repo.git.describe("--tags", "--abbrev=0", "HEAD")
            rev_range = f"{base}..HEAD"
            base_desc = f"tag {base}"
        except GitCommandError:
            rev_range = "HEAD"
            base_desc = "repository root"

        commits = [
            (str(commit.hexsha), str(commit.summary))
            for commit in repo.iter_commits(rev_range, max_count=max_count)
            if len(commit.parents) <= 1
        ]
    commits.reverse()  # oldest first, mirrors a changelog's reading order
    return commits, base_desc


def preset_spec_help() -> str:
    """Help text for the --commit-preset CLI option."""
    return (
        "Validate pending commits against a commit-message preset. "
        "Accepts a built-in name (conventional, commitizen, gitmoji-map) "
        "or a path to a custom TOML preset pack, optionally suffixed with "
        ":warn or :parse to override the mode (default: reject)."
    )
