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

import os
import re
from dataclasses import dataclass
from pathlib import Path
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
        return sum(
            1 for c in self.checks if c.failed() and c.severity == SEVERITY_WARNING
        )

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


_STALE_TRACKING_NOTE = (
    "not fetched (--offline or --noop); the local tracking ref may be stale"
)
_SHALLOW_NOTE = (
    "shallow clone: commit counts may be incomplete; run `git fetch --unshallow`"
)
# The userinfo part of a URL ("user:token@") is where HVCS tokens travel.
_URL_USERINFO = re.compile(r"(?<=://)[^/\s@]+@")


def _mask_credentials(text: str) -> str:
    return _URL_USERINFO.sub("****@", text)


@dataclass(frozen=True)
class _Upstream:
    """The configured upstream of a branch, as git reports it."""

    tracking_ref: str  # refs/remotes/origin/main, or refs/heads/x for remote "."
    remote: str  # remote name, "." for a local branch
    remote_ref: str  # refs/heads/main on the remote side

    @property
    def label(self) -> str:
        return self.tracking_ref.removeprefix("refs/remotes/").removeprefix(
            "refs/heads/"
        )

    @property
    def remote_branch(self) -> str:
        return self.remote_ref.removeprefix("refs/heads/")

    @property
    def is_local(self) -> bool:
        return self.remote == "."


def _sync_row(
    code: str, status: str, what: str, why: str = "", fix: str = ""
) -> CheckResult:
    return CheckResult(
        code=code,
        severity=SEVERITY_BLOCKER,
        status=status,
        what=what,
        why=why,
        fix=fix,
    )


def _upstream_of(repo: GitRepo, branch_ref: str) -> _Upstream | None:
    raw = repo.git.for_each_ref(
        "--format=%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)",
        branch_ref,
    )
    tracking_ref, remote, remote_ref = [*str(raw).split("\x00"), "", ""][:3]
    if not tracking_ref.strip():
        return None
    return _Upstream(tracking_ref.strip(), remote.strip(), remote_ref.strip())


def _fetch(repo: GitRepo, source: str, refspec: str) -> tuple[int, str]:
    """Fetch one ref without tags, submodules or prompts: (exit status, stderr)."""
    status, _stdout, stderr = repo.git.fetch(
        "--no-tags",
        "--no-recurse-submodules",
        "--quiet",
        source,
        refspec,
        with_extended_output=True,
        with_exceptions=False,
        env={"GIT_TERMINAL_PROMPT": "0"},
    )
    return int(status), str(stderr)


def _refresh_tracking_ref(
    repo: GitRepo, upstream: _Upstream, fetch_url: str | None
) -> str | None:
    """Update only the upstream's tracking ref; return the error, or None."""
    refspec = f"+{upstream.remote_ref}:{upstream.tracking_ref}"
    status, stderr = _fetch(repo, upstream.remote, refspec)
    configured = next((r.url for r in repo.remotes if r.name == upstream.remote), None)
    if status != 0 and fetch_url and fetch_url != configured:
        # The configured remote may lack credentials (e.g. a CI checkout that
        # does not persist them); retry once with the HVCS push URL.
        status, stderr = _fetch(repo, fetch_url, refspec)
    if status == 0:
        return None
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return _mask_credentials("; ".join(lines)) or f"git fetch exited with {status}"


def _resolve_commit(repo: GitRepo, ref: str) -> str | None:
    from git.exc import GitCommandError  # noqa: PLC0415

    try:
        return str(repo.git.rev_parse("--verify", "--quiet", f"{ref}^{{commit}}"))
    except GitCommandError:
        return None


def _ahead_behind(repo: GitRepo, head: str, upstream: str) -> tuple[int, int] | None:
    """Commits only on `head` and only on `upstream`; None when git cannot tell."""
    from git.exc import GitCommandError  # noqa: PLC0415

    try:
        out = repo.git.rev_list("--left-right", "--count", f"{head}...{upstream}")
        ahead, behind = (int(count) for count in str(out).split())
    except (GitCommandError, ValueError):
        return None
    return ahead, behind


def _no_upstream_row(repo: GitRepo, branch: str) -> CheckResult:
    remotes = [remote.name for remote in repo.remotes]
    if not remotes:
        return _sync_row(
            "BRANCH_SYNC",
            STATUS_SKIP,
            "no git remote; there is no upstream to compare the branch with "
            "(see MISSING_REMOTE)",
        )
    return _sync_row(
        "BRANCH_SYNC",
        STATUS_SKIP,
        f"branch '{branch}' has no upstream branch configured",
        why="without an upstream the branch cannot be compared with the remote",
        fix=f"git branch --set-upstream-to={remotes[0]}/{branch}",
    )


def _out_of_sync_row(
    branch: str, upstream: _Upstream, ahead: int, behind: int, notes: list[str]
) -> CheckResult:
    if ahead and behind:
        code = "BRANCH_DIVERGED"
        what = (
            f"branch '{branch}' has diverged: {ahead} ahead and {behind} behind "
            f"'{upstream.label}'"
        )
        why = (
            "local and upstream history both moved; the release would tag a "
            "history the remote does not have, and its push would be rejected"
        )
        fix = "git pull --rebase"
    elif behind:
        code = "BRANCH_BEHIND"
        what = f"branch '{branch}' is {behind} commit(s) behind '{upstream.label}'"
        why = (
            "the release would be computed from outdated history and miss the "
            "newer upstream commits; its push would be rejected"
        )
        fix = "git pull --ff-only"
    else:
        code = "BRANCH_AHEAD"
        what = f"branch '{branch}' is {ahead} commit(s) ahead of '{upstream.label}'"
        why = (
            "the release would include commits that are not on the remote yet, so "
            "they have not been reviewed or tested there; push them and let CI pass "
            "before releasing"
        )
        fix = f"git push {upstream.remote} HEAD:{upstream.remote_branch}"
    return _sync_row(code, STATUS_FAIL, what, "; ".join([why, *notes]), fix)


def _compare_with_upstream(
    repo: GitRepo, branch: str, head_sha: str, upstream: _Upstream, *, fetched: bool
) -> CheckResult:
    upstream_sha = _resolve_commit(repo, upstream.tracking_ref)
    if upstream_sha is None:
        return _sync_row(
            "BRANCH_SYNC",
            STATUS_SKIP,
            f"upstream '{upstream.label}' has no local tracking ref to compare with",
            why="the upstream branch was never fetched into this clone",
            fix=f"git fetch {upstream.remote}",
        )
    stale = not fetched and not upstream.is_local
    if upstream_sha == head_sha and stale:
        # Matching a tracking ref that was not refreshed proves nothing about
        # the remote, so this is a skip (like the offline registry check),
        # not a pass. A skip also shows its reason in the table output.
        return _sync_row(
            "BRANCH_SYNC",
            STATUS_SKIP,
            f"branch '{branch}' matches the local tracking ref of '{upstream.label}'",
            why=_STALE_TRACKING_NOTE,
            fix="re-run without --offline or --noop to fetch and confirm",
        )
    if upstream_sha == head_sha:
        return _sync_row(
            "BRANCH_SYNC",
            STATUS_PASS,
            f"branch '{branch}' is in sync with '{upstream.label}'",
        )
    notes = [_STALE_TRACKING_NOTE] if stale else []
    if repo.git.rev_parse("--is-shallow-repository") == "true":
        notes.append(_SHALLOW_NOTE)
    counts = _ahead_behind(repo, head_sha, upstream_sha)
    if counts is None:
        return _sync_row(
            "BRANCH_SYNC_UNCONFIRMED",
            STATUS_FAIL,
            f"could not count the commits between '{branch}' and '{upstream.label}'",
            why="; ".join(["git rev-list failed on two different commits", *notes]),
            fix=f"git fetch {upstream.remote}, then re-run",
        )
    return _out_of_sync_row(branch, upstream, *counts, notes)


def _branch_sync(repo: GitRepo, *, offline: bool, fetch_url: str | None) -> CheckResult:
    if repo.head.is_detached:
        return _sync_row(
            "BRANCH_SYNC",
            STATUS_SKIP,
            "HEAD is detached; there is no branch to compare with an upstream",
            why="CI checkouts of a single commit are detached",
            fix="check out the release branch to compare it with the remote",
        )
    branch = repo.head.ref
    try:
        head_sha = str(branch.commit.hexsha)
    except ValueError:
        return _sync_row(
            "BRANCH_SYNC", STATUS_SKIP, f"branch '{branch.name}' has no commits yet"
        )
    upstream = _upstream_of(repo, branch.path)
    if upstream is None:
        return _no_upstream_row(repo, branch.name)
    fetched = False
    if not offline and not upstream.is_local:
        error = _refresh_tracking_ref(repo, upstream, fetch_url)
        if error is not None:
            return _sync_row(
                "BRANCH_SYNC_UNCONFIRMED",
                STATUS_FAIL,
                f"could not fetch '{upstream.label}' to compare branch "
                f"'{branch.name}' with it",
                why=error,
                fix=f"check network access and credentials for remote "
                f"'{upstream.remote}', or re-run with --offline to compare with "
                "the local tracking ref",
            )
        fetched = True
    return _compare_with_upstream(
        repo, branch.name, head_sha, upstream, fetched=fetched
    )


def check_branch_sync(
    repo_dir: Path | str,
    *,
    offline: bool = False,
    fetch_url: str | None = None,
) -> CheckResult:
    """
    HEAD must match its upstream branch before a release is cut.

    Online, the upstream branch is fetched first: only its remote-tracking ref
    is updated (no tags, no working tree, no local branch). `fetch_url` is a
    fallback source used when fetching the configured remote fails, such as an
    HVCS URL that carries the token. Offline, nothing is fetched and the local
    tracking ref is compared as is, so the result may be stale.

    Behind, diverged and ahead all fail with blocker severity: each one means
    the release would be cut from a history that differs from the remote.
    A missing remote, missing upstream, detached HEAD or unborn branch is a
    skip, not a crash; an unreachable remote fails closed.
    """
    from git import Repo  # noqa: PLC0415
    from git.exc import InvalidGitRepositoryError, NoSuchPathError  # noqa: PLC0415

    try:
        repo = Repo(str(repo_dir))
    except (InvalidGitRepositoryError, NoSuchPathError):
        return _sync_row(
            "BRANCH_SYNC", STATUS_SKIP, f"not a git repository: {repo_dir}"
        )
    with repo:
        return _branch_sync(repo, offline=offline, fetch_url=fetch_url)


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

    readable = [
        (label, value)
        for label, value in rows
        if value and not value.startswith("unreadable:")
    ]
    missing = [label for label, value in rows if value in {None, "no version found"}]
    unreadable = [
        label for label, value in rows if value and value.startswith("unreadable:")
    ]

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
            why="; ".join(problems)
            + (f"; agreed elsewhere: {agreed}" if agreed else ""),
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


_MANIFEST_FILENAMES = frozenset(
    {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
)
_MANIFEST_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        "target",
    }
)


def _normalize_declared_prefix(raw_path: str) -> str:
    cleaned = raw_path.replace("\\", "/").strip().lstrip("./").rstrip("/")
    while cleaned.endswith(("/*", "/**")):
        cleaned = cleaned.rsplit("/", 1)[0]
    return "" if cleaned in {".", "*"} else cleaned


def _declared_component_prefixes(bsr_config: BsrConfig) -> tuple[str, ...]:
    prefixes: set[str] = set()
    for component in bsr_config.components:
        for path in component.paths:
            norm = _normalize_declared_prefix(path)
            if norm:
                prefixes.add(norm)
    path_map = bsr_config.component_path_map
    if path_map is not None:
        for mapping in path_map.components:
            for root in mapping.roots:
                norm = _normalize_declared_prefix(root)
                if norm:
                    prefixes.add(norm)
        for rule in path_map.rules:
            norm = _normalize_declared_prefix(rule.path)
            if norm:
                prefixes.add(norm)
    return tuple(sorted(prefixes))


def _is_manifest_covered(
    rel_dir: str, rel_file: str, prefixes: tuple[str, ...]
) -> bool:
    for prefix in prefixes:
        if rel_dir == prefix or rel_dir.startswith(f"{prefix}/") or rel_file == prefix:
            return True
    return False


def check_monorepo_undeclared_manifests(
    repo_root: Path, bsr_config: BsrConfig
) -> CheckResult:
    """Warn when subdirectory package manifests sit outside declared components."""
    if bsr_config.component_path_map is None and not bsr_config.components:
        return CheckResult(
            code="MONOREPO_MANIFESTS",
            severity=SEVERITY_WARNING,
            status=STATUS_SKIP,
            what="no monorepo components configured",
        )

    prefixes = _declared_component_prefixes(bsr_config)
    uncovered: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in _MANIFEST_SKIP_DIRS)
        rel_dir = Path(dirpath).relative_to(repo_root).as_posix()
        if rel_dir == ".":
            continue
        for filename in sorted(filenames):
            if filename not in _MANIFEST_FILENAMES:
                continue
            rel_file = f"{rel_dir}/{filename}"
            if not _is_manifest_covered(rel_dir, rel_file, prefixes):
                uncovered.append(rel_file)

    if uncovered:
        return CheckResult(
            code="MONOREPO_MANIFESTS",
            severity=SEVERITY_WARNING,
            status=STATUS_FAIL,
            what=f"{len(uncovered)} package manifest(s) outside declared components",
            why=", ".join(uncovered),
            fix="add each package path to [tool.semantic_release.bsr.components] or component_path_map",
        )

    return CheckResult(
        code="MONOREPO_MANIFESTS",
        severity=SEVERITY_WARNING,
        status=STATUS_PASS,
        what="all discovered subdirectory manifests match declared components",
    )
