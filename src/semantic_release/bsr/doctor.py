"""
Structured self-diagnosis checks (`bsr.doctor`).

Task 3 of the universal-release-engine plan. Two check layers, one catalog:

- **policy/safety blockers** — a publish must not continue (orphan tag,
  registry collision, missing remote, branch not a release branch, invalid
  config);
- **diagnostics/warnings** — setup has problems but they do not certainly
  block every command (unparseable tags, token readiness, prerelease
  mismatch, version-target drift).

Every check returns a :class:`CheckResult` with `code`, `severity`, `status`,
`what`, `why`, `fix` — the same spirit as ``actionable_errors``, but uniform
and machine-readable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Sequence

from semantic_release.bsr.guards import resolve_registry
from semantic_release.bsr.registry import ProbeResult, probe_registry

if TYPE_CHECKING:  # pragma: no cover

    from git import Repo as GitRepo

    from semantic_release.bsr.config import BsrConfig
    from semantic_release.cli.config import RuntimeContext
    from semantic_release.version.translator import VersionTranslator
    from semantic_release.version.version import Version

SEVERITY_BLOCKER = "blocker"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

STATUS_PASS = "pass"  # noqa: S105 - status label, not a secret
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"

SCHEMA_VERSION = 1

# Registry probing is injected so `verify`/`doctor` callers decide live vs
# offline, and tests can stub it. Signature mirrors `probe_registry`.
RegistryProbe = Callable[[str, str, str], ProbeResult]

_LIVE_PROBE: RegistryProbe = probe_registry


@dataclass(frozen=True)
class CheckResult:
    """One diagnosis row: machine-readable and human-actionable."""

    code: str
    severity: str
    status: str
    what: str
    why: str = ""
    fix: str = ""

    def failed(self) -> bool:
        return self.status == STATUS_FAIL


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[CheckResult, ...]

    @property
    def blocked(self) -> bool:
        return any(c.failed() and c.severity == SEVERITY_BLOCKER for c in self.checks)

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if c.failed() and c.severity == SEVERITY_WARNING)

    def to_document(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "blocked": self.blocked,
            "warning_count": self.warning_count,
            "checks": [
                {
                    "code": c.code,
                    "severity": c.severity,
                    "status": c.status,
                    "what": c.what,
                    "why": c.why,
                    "fix": c.fix,
                }
                for c in self.checks
            ],
        }

    def render_table(self) -> str:
        lines = ["better-semantic-release doctor", *(_row(c) for c in self.checks)]
        if self.blocked:
            lines.append("RESULT: BLOCKED")
        elif self.warning_count:
            lines.append(f"RESULT: {self.warning_count} warning(s)")
        else:
            lines.append("RESULT: OK")
        return "\n".join(lines)

    def render_markdown(self) -> str:
        lines = [
            "## Doctor report",
            "",
            f"blocked: {'yes' if self.blocked else 'no'}",
            "",
            "| code | severity | status | what | fix |",
            "|---|---|---|---|---|",
            *(
                f"| {c.code} | {c.severity} | {c.status} | {c.what} | {c.fix} |"
                for c in self.checks
            ),
        ]
        return "\n".join(lines)


def _row(c: CheckResult) -> str:
    detail = f" -- {c.why}" if c.why and c.status != STATUS_PASS else ""
    fix = f" [fix: {c.fix}]" if c.fix and c.status == STATUS_FAIL else ""
    return f"  [{c.status:>4}] {c.severity:<7} {c.code:<24}{detail}{fix}"


# ---------------------------------------------------------------------------
# individual checks (all read-only; repo access via explicit args)


def check_missing_remote(repo: GitRepo) -> CheckResult:
    remotes = list(repo.remotes)
    if remotes:
        return CheckResult(
            code="MISSING_REMOTE",
            severity=SEVERITY_BLOCKER,
            status=STATUS_PASS,
            what=f"git remote '{remotes[0].name}' is configured",
        )
    return CheckResult(
        code="MISSING_REMOTE",
        severity=SEVERITY_BLOCKER,
        status=STATUS_FAIL,
        what="no git remote is configured",
        why="version cannot push commits/tags or create a VCS release without a remote",
        fix="git remote add origin <url>",
    )


def check_tag_format(repo: GitRepo, translator: VersionTranslator) -> CheckResult:
    """Unparseable tags usually mean the tag format changed mid-history."""
    from semantic_release.errors import InvalidVersion  # noqa: PLC0415

    unparseable = []
    for tag in repo.tags:
        try:
            parsed = translator.from_tag(str(tag))
        except InvalidVersion:
            unparseable.append(str(tag))
            continue
        if parsed is None:
            unparseable.append(str(tag))
    if not unparseable:
        return CheckResult(
            code="TAG_FORMAT_MISMATCH",
            severity=SEVERITY_WARNING,
            status=STATUS_PASS,
            what="all existing tags parse under the configured tag format",
        )
    sample = ", ".join(unparseable[:5]) + ("..." if len(unparseable) > 5 else "")
    return CheckResult(
        code="TAG_FORMAT_MISMATCH",
        severity=SEVERITY_WARNING,
        status=STATUS_FAIL,
        what=f"{len(unparseable)} tag(s) do not parse under tag_format "
        f"'{translator.tag_format}' ({sample})",
        why="these tags are invisible to the release algorithm; a format change "
        "mid-history can silently bump from the wrong base version",
        fix="align tag_format with history, or accept the ignored tags explicitly",
    )


def check_branch_config(
    match_patterns: Sequence[str],
    active_branch: str,
) -> CheckResult:
    """The active branch must match one configured release branch pattern."""
    for pattern in match_patterns:
        if re.search(pattern, active_branch):
            return CheckResult(
                code="BRANCH_CONFIG",
                severity=SEVERITY_BLOCKER,
                status=STATUS_PASS,
                what=f"active branch '{active_branch}' matches a configured release branch",
            )
    return CheckResult(
        code="BRANCH_CONFIG",
        severity=SEVERITY_BLOCKER,
        status=STATUS_FAIL,
        what=f"active branch '{active_branch}' does not match a configured release branch",
        why="version refuses to release from a branch outside the configured patterns",
        fix="run from a release branch, or add a [tool.semantic_release.branches] entry",
    )


def check_hvcs_token(hvcs_client: object, *, needs_release: bool) -> CheckResult:
    token = getattr(hvcs_client, "token", None)
    if token:
        return CheckResult(
            code="HVCS_TOKEN",
            severity=SEVERITY_WARNING,
            status=STATUS_PASS,
            what="HVCS token is configured",
        )
    severity = SEVERITY_BLOCKER if needs_release else SEVERITY_WARNING
    return CheckResult(
        code="HVCS_TOKEN",
        severity=severity,
        status=STATUS_FAIL,
        what="no HVCS token is configured",
        why="creating the VCS release will fail without a token",
        fix="set the token via the standard HVCS environment variable "
        "or [tool.semantic_release.hvcs]",
    )


def check_prerelease_consistency(
    active_branch_config_prerelease: bool,
    new_version: Version,
) -> CheckResult:
    is_pre = bool(new_version.is_prerelease)
    if is_pre == active_branch_config_prerelease:
        return CheckResult(
            code="PRERELEASE_MISMATCH",
            severity=SEVERITY_WARNING,
            status=STATUS_PASS,
            what="computed version prerelease-ness matches the branch config",
        )
    return CheckResult(
        code="PRERELEASE_MISMATCH",
        severity=SEVERITY_WARNING,
        status=STATUS_FAIL,
        what=f"computed version is {'a prerelease' if is_pre else 'a final release'} "
        f"while the branch config expects {'a prerelease' if active_branch_config_prerelease else 'a final release'}",
        why="branch prerelease flag and the computed version disagree; "
        "publish channels can get a version of the wrong kind",
        fix="align [tool.semantic_release.branches] prerelease setting with intent",
    )


def check_registry_state(
    bsr_cfg: BsrConfig,
    project_name: str,
    version_str: str,
    *,
    probe: RegistryProbe | None,
) -> CheckResult:
    """Registry collision/unknown checks; offline when `probe` is None."""
    code_base = "REGISTRY"
    try:
        registry = resolve_registry(bsr_cfg, project_name)
    except Exception as exc:  # noqa: BLE001 - config errors surface as checks here
        return CheckResult(
            code="BAD_REGISTRY_CONFIG",
            severity=SEVERITY_BLOCKER,
            status=STATUS_FAIL,
            what="bsr registry setting is invalid",
            why=str(exc),
            fix="set [tool.semantic_release.bsr] registry to one of: pypi, npm, none",
        )
    if registry == "none":
        return CheckResult(
            code=code_base,
            severity=SEVERITY_BLOCKER,
            status=STATUS_SKIP,
            what="registry checks disabled (registry='none')",
        )
    if probe is None:
        return CheckResult(
            code=code_base,
            severity=SEVERITY_BLOCKER,
            status=STATUS_SKIP,
            what=f"registry '{registry}' not probed (offline default)",
            why="a live probe is network-nondeterministic; `version` still enforces "
            "the guard live before any persistence",
            fix="re-run with --check-registry to probe now",
        )
    result = probe(registry, project_name, version_str)
    if result is ProbeResult.FREE:
        return CheckResult(
            code=code_base,
            severity=SEVERITY_BLOCKER,
            status=STATUS_PASS,
            what=f"{project_name}@{version_str} is free on {registry}",
        )
    if result is ProbeResult.EXISTS:
        return CheckResult(
            code="REGISTRY_COLLISION",
            severity=SEVERITY_BLOCKER,
            status=STATUS_FAIL,
            what=f"{project_name}@{version_str} already exists on {registry}",
            why="publishing would re-publish an existing version (history rewrite or collision)",
            fix="bump the version or resolve the collision",
        )
    return CheckResult(
        code="REGISTRY_PROBE_UNKNOWN",
        severity=SEVERITY_BLOCKER,
        status=STATUS_FAIL,
        what=f"could not confirm whether {project_name}@{version_str} is free on {registry}",
        why="treating UNKNOWN as free risks a double publish (fail-closed)",
        fix="retry, or set [tool.semantic_release.bsr] registry='none' if intentional",
    )


def _declaration_label(decl: object) -> str:
    path = getattr(decl, "_path", None) or getattr(decl, "path", None)
    return str(path) if path is not None else "<unknown>"


def _declared_versions(decl: object) -> set[str]:
    """Read the currently stamped version(s) from one declaration, read-only."""
    # Pattern/file declarations expose a working parse().
    if type(decl).__name__ != "TomlVersionDeclaration":
        try:
            return {str(v) for v in decl.parse()}  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - a broken declaration is the diagnosis
            return set()
    # TomlVersionDeclaration.parse is deprecated and dead upstream; read the
    # key the same way its own replace() does.
    try:
        import tomlkit  # noqa: PLC0415
        from dotty_dict import Dotty  # noqa: PLC0415

        doc = tomlkit.loads(str(decl.content))  # type: ignore[attr-defined]
        search_key = str(getattr(decl, "_search_text", ""))
        value = Dotty(doc).get(search_key) if search_key else None  # noqa: SLF001
        if isinstance(value, str) and value.strip():
            return {value.strip()}
    except Exception:  # noqa: BLE001 - unreadable declaration is the diagnosis
        return set()
    return set()


def check_version_targets(
    runtime: RuntimeContext,
    previous_version: str | None,
) -> CheckResult:
    """Configured version declarations should carry the last released version."""
    if previous_version is None:
        return CheckResult(
            code="VERSION_TARGETS",
            severity=SEVERITY_WARNING,
            status=STATUS_SKIP,
            what="no released version yet; declaration consistency not applicable",
        )
    if not runtime.version_declarations:
        return CheckResult(
            code="VERSION_TARGETS",
            severity=SEVERITY_WARNING,
            status=STATUS_PASS,
            what="no version declarations configured",
        )
    expected = previous_version
    stale: list[str] = [
        _declaration_label(decl)
        for decl in runtime.version_declarations
        if expected not in _declared_versions(decl)
    ]
    if not stale:
        return CheckResult(
            code="VERSION_TARGETS",
            severity=SEVERITY_WARNING,
            status=STATUS_PASS,
            what="all version declarations carry the last released version",
        )
    return CheckResult(
        code="VERSION_TARGETS",
        severity=SEVERITY_WARNING,
        status=STATUS_FAIL,
        what="version declarations do not match the last released version",
        why=f"declaration(s) {', '.join(stale)} do not contain {expected}; "
        "the next release may write from a stale base",
        fix="sync the declarations to the last released version before releasing",
    )


def check_trusted_publishing(bsr_cfg: BsrConfig) -> CheckResult:
    del bsr_cfg  # trusted publishing is a publish-command concern today
    return CheckResult(
        code="TRUSTED_PUBLISHING",
        severity=SEVERITY_INFO,
        status=STATUS_SKIP,
        what="trusted publishing readiness detection not wired for this HVCS",
        why="BSR enforces it in the publish command when configured",
        fix="no action required",
    )


def check_version_consistency(sources: Sequence[object]) -> CheckResult:
    """
    Explicit or bridged version sources must agree on the current version.

    Task 4 Step 4: when the same project version lives in several places
    (``pyproject.toml``, ``package.json``, a ``VERSION`` file, git tags) and
    they diverge, the release would stamp from the wrong base. Each source is
    named with its provenance so the drift is actionable at a glance.
    """
    from semantic_release.bsr.version_sources import VersionSource

    if not sources:
        return CheckResult(
            code="VERSION_CONSISTENCY",
            severity=SEVERITY_WARNING,
            status=STATUS_SKIP,
            what="no version sources to cross-check",
        )

    rows: list[tuple[str, str | None]] = []
    for source in sources:
        if not isinstance(source, VersionSource):  # pragma: no cover
            continue
        prov = source.provenance()
        label = f"{prov.kind}:{prov.location}"
        if prov.detail:
            label = f"{label} ({prov.detail})"
        try:
            versions = source.load()
        except Exception as exc:  # noqa: BLE001 - unreadable source IS the finding
            rows.append((label, f"unreadable: {exc}"))
            continue
        if not versions:
            rows.append((label, "no version found"))
            continue
        rows.append((label, str(max(versions))))

    if not rows:
        return CheckResult(
            code="VERSION_CONSISTENCY",
            severity=SEVERITY_WARNING,
            status=STATUS_SKIP,
            what="no version sources to cross-check",
        )

    readable = [(label, value) for label, value in rows if value and not value.startswith("unreadable:")]
    missing = [label for label, value in rows if value in {None, "no version found"}]
    unreadable = [label for label, value in rows if value and value.startswith("unreadable:")]

    if len(readable) >= 2 and len({value for _, value in readable}) > 1:
        detail = "; ".join(f"{label} -> {value}" for label, value in readable)
        return CheckResult(
            code="VERSION_CONSISTENCY",
            severity=SEVERITY_WARNING,
            status=STATUS_FAIL,
            what="version sources disagree on the current version",
            why=f"{detail}"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unreadable: {', '.join(unreadable)}" if unreadable else ""),
            fix="sync the divergent sources to one version before releasing",
        )

    if missing or unreadable:
        problems = [
            *(f"{label}: no version found" for label in missing),
            *(f"{label}: unreadable" for label in unreadable),
        ]
        agreed = "; ".join(f"{label} -> {value}" for label, value in readable)
        return CheckResult(
            code="VERSION_CONSISTENCY",
            severity=SEVERITY_WARNING,
            status=STATUS_FAIL,
            what="one or more version sources carry no version",
            why="; ".join(problems) + (f"; agreed elsewhere: {agreed}" if agreed else ""),
            fix="populate or remove the empty sources, or fix their kind/path config",
        )

    agreed = "; ".join(f"{label} -> {value}" for label, value in readable)
    return CheckResult(
        code="VERSION_CONSISTENCY",
        severity=SEVERITY_WARNING,
        status=STATUS_PASS,
        what=f"all {len(readable)} version source(s) agree",
        why=agreed,
    )
