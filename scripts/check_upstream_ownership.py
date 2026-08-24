# ruff: noqa: S603, S607, T201
"""Validate stock ownership and run quality gates from the ownership manifest."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

try:
    import tomlkit
except ModuleNotFoundError:  # pragma: no cover - CI installs project dependencies
    import tomllib

    class _TomlParser:
        @staticmethod
        def parse(document: str) -> dict[str, object]:
            return tomllib.loads(document)

    tomlkit = _TomlParser()

COVERAGE_GATES = (
    "python-lint",
    "python-format",
    "python-type",
    "python-test",
    "workflows",
    "docs",
    "other",
)
PYTHON_GATES = COVERAGE_GATES[:4]
_PYTHON_KINDS = frozenset({"python-source", "python-test", "python-tool"})
_OWNERSHIP_STATUSES = frozenset({"A", "C", "D", "M", "R", "T"})
_DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1] / "config" / "bsr-upstream-ownership.toml"
)


@dataclass(frozen=True)
class OwnershipEntry:
    """One file-level ownership declaration from the manifest."""

    path: str
    status: str
    kind: str
    owner: str
    reason: str
    gates: tuple[str, ...]


@dataclass(frozen=True)
class OwnershipManifest:
    """Parsed ownership policy and the root it governs."""

    path: Path
    root: Path
    baseline: str
    marker: str
    ignored_paths: tuple[str, ...]
    entries: tuple[OwnershipEntry, ...]


@dataclass(frozen=True)
class CoverageCount:
    """Eligible and manifest-covered files for one independent gate."""

    eligible: int
    covered: int


@dataclass(frozen=True)
class OwnershipReport:
    """Validation result, including separate counts for every gate class."""

    changed_files: tuple[str, ...]
    errors: tuple[str, ...]
    coverage: dict[str, CoverageCount]


def _as_text(value: object, field: str, path: Path) -> str:
    if not isinstance(value, str):
        value = getattr(value, "value", value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {field} must be a non-empty string")
    return value


def _as_strings(value: object, field: str, path: Path) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or value is None:
        raise ValueError(f"{path}: {field} must be an array")
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{path}: {field} must be an array") from exc
    return tuple(_as_text(item, field, path) for item in values)


def load_manifest(path: Path) -> OwnershipManifest:
    """Read and validate one ownership manifest."""
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    baseline = _as_text(document.get("baseline"), "baseline", path)
    marker = _as_text(document.get("marker", "BSR-PATCH"), "marker", path)

    policy = document.get("policy", {})
    ignored_paths = _as_strings(policy.get("ignored_paths", []), "ignored_paths", path)

    entries: list[OwnershipEntry] = []
    raw_entries = document.get("ownership", [])
    if isinstance(raw_entries, (str, bytes)) or raw_entries is None:
        raise ValueError(f"{path}: ownership must be an array of tables")
    for raw_entry in raw_entries:
        entry_path = _as_text(raw_entry.get("path"), "ownership.path", path)
        status = _as_text(raw_entry.get("status"), "ownership.status", path)
        kind = _as_text(raw_entry.get("kind"), "ownership.kind", path)
        owner = _as_text(raw_entry.get("owner"), "ownership.owner", path)
        reason = _as_text(raw_entry.get("reason"), "ownership.reason", path)
        gates = _as_strings(raw_entry.get("gates", []), "ownership.gates", path)
        entries.append(
            OwnershipEntry(
                path=entry_path,
                status=status,
                kind=kind,
                owner=owner,
                reason=reason,
                gates=gates,
            )
        )

    root = path.parent.parent if path.parent.name == "config" else path.parent
    return OwnershipManifest(
        path=path,
        root=root,
        baseline=baseline,
        marker=marker,
        ignored_paths=ignored_paths,
        entries=tuple(entries),
    )


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _normalise_status_line(line: str) -> tuple[str, str]:
    status, path = line.split("\t", 1)
    if status.startswith(("R", "C")):
        path = path.rsplit("\t", 1)[-1]
        status = status[0]
    return status[0], path.replace("\\", "/")


def _is_ignored(path: str, ignored_paths: Iterable[str]) -> bool:
    return any(
        path == ignored or path.startswith(ignored.rstrip("/") + "/")
        for ignored in ignored_paths
    )


def collect_changed_statuses(manifest: OwnershipManifest) -> dict[str, str]:
    """Return normalized Git statuses for files differing from the baseline."""
    tracked = [
        _normalise_status_line(line)
        for line in _run_git(
            manifest.root, "diff", "--name-status", manifest.baseline
        ).splitlines()
        if line.strip()
    ]
    tracked_paths = {path for _, path in tracked}
    untracked = [
        ("A", path.replace("\\", "/"))
        for path in _run_git(
            manifest.root, "ls-files", "--others", "--exclude-standard"
        ).splitlines()
        if path.strip() and path.replace("\\", "/") not in tracked_paths
    ]
    return dict(
        sorted(
            (path, status)
            for status, path in (*tracked, *untracked)
            if not _is_ignored(path, manifest.ignored_paths)
        )
    )


def collect_changed_files(manifest: OwnershipManifest) -> tuple[str, ...]:
    """Return all tracked and untracked files differing from the frozen baseline."""
    return tuple(collect_changed_statuses(manifest))


def _eligible(entry: OwnershipEntry, gate: str) -> bool:
    if gate in {"python-lint", "python-format"}:
        return entry.kind in _PYTHON_KINDS
    if gate == "python-type":
        return entry.kind == "python-source"
    if gate == "python-test":
        return entry.kind == "python-test"
    return entry.kind == gate


def scope_for_gate(manifest: OwnershipManifest, gate: str) -> tuple[str, ...]:
    """Return the manifest-derived file scope for one gate."""
    if gate not in COVERAGE_GATES:
        raise ValueError(f"unknown ownership gate: {gate}")
    return tuple(entry.path for entry in manifest.entries if gate in entry.gates)


def coverage_counts(
    manifest: OwnershipManifest,
) -> dict[str, CoverageCount]:
    """Calculate independent eligible/covered counts for every gate."""
    counts: dict[str, CoverageCount] = {}
    for gate in COVERAGE_GATES:
        eligible_entries = [
            entry for entry in manifest.entries if _eligible(entry, gate)
        ]
        covered = sum(gate in entry.gates for entry in eligible_entries)
        counts[gate] = CoverageCount(
            eligible=len(eligible_entries),
            covered=covered,
        )
    return counts


def _marker_files(manifest: OwnershipManifest) -> set[str]:
    source_root = manifest.root / "src"
    if not source_root.exists():
        return set()
    return {
        path.relative_to(manifest.root).as_posix()
        for path in source_root.rglob("*.py")
        if manifest.marker in path.read_text(encoding="utf-8")
    }


def _normalise_changed_paths(
    manifest: OwnershipManifest,
    changed_files: Sequence[str] | Mapping[str, str] | None,
) -> tuple[str, ...]:
    paths = (
        changed_files if changed_files is not None else collect_changed_files(manifest)
    )
    return tuple(
        sorted(
            path.replace("\\", "/")
            for path in paths
            if not _is_ignored(path, manifest.ignored_paths)
        )
    )


def _difference_errors(
    manifest: OwnershipManifest,
    actual: tuple[str, ...],
    by_path: dict[str, OwnershipEntry],
    has_override: bool,
) -> list[str]:
    errors: list[str] = []
    duplicates = sorted(
        path
        for path in {entry.path for entry in manifest.entries}
        if sum(entry.path == path for entry in manifest.entries) > 1
    )
    if duplicates:
        errors.append(f"duplicate manifest entries: {', '.join(duplicates)}")

    unowned = sorted(set(actual) - set(by_path))
    if unowned:
        errors.append(f"unowned changed files: {', '.join(unowned)}")

    if not has_override:
        stale = sorted(set(by_path) - set(actual))
        if stale:
            errors.append(f"manifest files not in baseline diff: {', '.join(stale)}")
    return errors


def _status_errors(
    by_path: Mapping[str, OwnershipEntry],
    actual_statuses: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    for path, actual_status in actual_statuses.items():
        entry = by_path.get(path)
        if entry is not None and entry.status != actual_status:
            errors.append(
                f"ownership status mismatch: {path} "
                f"(manifest={entry.status}, actual={actual_status})"
            )
    return errors


def _entry_errors(manifest: OwnershipManifest) -> list[str]:
    errors: list[str] = []
    for entry in manifest.entries:
        unknown_gates = sorted(set(entry.gates) - set(COVERAGE_GATES))
        if unknown_gates:
            errors.append(f"{entry.path}: unknown gates: {', '.join(unknown_gates)}")
        if entry.status not in _OWNERSHIP_STATUSES:
            errors.append(f"{entry.path}: unknown ownership status: {entry.status}")
        if not entry.gates:
            errors.append(f"{entry.path}: no ownership gates declared")
        if entry.kind == "python-source" and not entry.reason.strip():
            errors.append(f"{entry.path}: stock source ownership requires a reason")
    return errors


def _coverage_errors(manifest: OwnershipManifest) -> list[str]:
    errors: list[str] = []
    for gate, counts in coverage_counts(manifest).items():
        if counts.eligible != counts.covered:
            errors.append(
                f"{gate} coverage mismatch: eligible={counts.eligible}, "
                f"covered={counts.covered}"
            )
    return errors


def _marker_errors(
    manifest: OwnershipManifest,
    by_path: dict[str, OwnershipEntry],
) -> list[str]:
    marked = _marker_files(manifest)
    unlisted_markers = sorted(marked - set(by_path))
    if not unlisted_markers:
        return []
    return ["marked source files missing from manifest: " + ", ".join(unlisted_markers)]


def validate_ownership(
    manifest: OwnershipManifest,
    changed_files: Sequence[str] | Mapping[str, str] | None = None,
) -> OwnershipReport:
    """Validate completeness, statuses, reasons, markers, and gate coverage."""
    if changed_files is None:
        actual_statuses: Mapping[str, str] | None = collect_changed_statuses(manifest)
        has_override = False
    elif isinstance(changed_files, Mapping):
        actual_statuses = {
            path.replace("\\", "/"): status
            for path, status in changed_files.items()
            if not _is_ignored(path.replace("\\", "/"), manifest.ignored_paths)
        }
        has_override = True
    else:
        actual_statuses = None
        has_override = True

    actual = _normalise_changed_paths(
        manifest,
        actual_statuses if actual_statuses is not None else changed_files,
    )
    by_path = {entry.path: entry for entry in manifest.entries}
    errors = _difference_errors(
        manifest,
        actual,
        by_path,
        has_override=has_override,
    )
    if actual_statuses is not None:
        errors.extend(_status_errors(by_path, actual_statuses))
    errors.extend(_entry_errors(manifest))
    errors.extend(_coverage_errors(manifest))
    errors.extend(_marker_errors(manifest, by_path))
    return OwnershipReport(
        changed_files=actual,
        errors=tuple(sorted(set(errors))),
        coverage=coverage_counts(manifest),
    )


def _gate_command(manifest: OwnershipManifest, gate: str) -> list[str]:
    scope = list(scope_for_gate(manifest, gate))
    if gate == "python-lint":
        return [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            "pyproject.toml",
            *scope,
        ]
    if gate == "python-format":
        return [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--config",
            "pyproject.toml",
            *scope,
        ]
    if gate == "python-type":
        return [sys.executable, "-m", "mypy", *scope]
    if gate == "python-test":
        return [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov=semantic_release.bsr",
            "--cov-report=xml",
            *scope,
        ]
    raise ValueError(f"gate {gate} has no executable command")


def _print_report(report: OwnershipReport) -> None:
    for gate, counts in report.coverage.items():
        print(f"{gate}: eligible={counts.eligible} covered={counts.covered}")
    if report.errors:
        for error in report.errors:
            print(f"ownership error: {error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Run validation or one manifest-derived Python gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--check", action="store_true", help="validate ownership only")
    parser.add_argument("--run-gate", choices=PYTHON_GATES)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest.resolve())
    report = validate_ownership(manifest)
    _print_report(report)
    if report.errors:
        return 1
    if not args.run_gate:
        return 0

    command = _gate_command(manifest, args.run_gate)
    return subprocess.run(command, cwd=manifest.root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
