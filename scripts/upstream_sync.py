# ruff: noqa: S603, S607, T201
"""
Upstream sync harness (wave-2 W2.4).

Compares the bsr fork against upstream python-semantic-release **by module**
and maintains a deterministic golden inventory of the divergence so both
manual syncs and CI can answer one question: *given the upstream ref we
track, which stock files changed upstream, which of those are diverged
locally, and which local paths are fork-owned and must never be touched by
a sync?*

Network seam
    ``fetch_upstream_tree`` shells out to git (``fetch`` + ``ls-tree``) and
    is the ONLY touching-the-network path. Everything else is pure and
    injectable: tests pass an in-memory tree mapping and never spawn git.

Golden contract
    ``--refresh`` writes ``config/bsr-upstream-sync-golden.json`` with
    sorted keys and NO timestamps, so refreshing twice at the same upstream
    ref is byte-identical (idempotent). ``--check`` recomputes and compares;
    any drift (upstream moved, local file changed) fails with a diff.

Usage::

    python scripts/upstream_sync.py --check          # CI/manual drift gate
    python scripts/upstream_sync.py --refresh        # pin the new inventory
    python scripts/upstream_sync.py --json           # machine-readable report
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

try:
    from scripts.check_upstream_ownership import (  # type: ignore[import-not-found]
        load_manifest,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from check_upstream_ownership import (  # type: ignore[no-redef, import-not-found]
        load_manifest,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "config" / "bsr-upstream-ownership.toml"
DEFAULT_GOLDEN = REPO_ROOT / "config" / "bsr-upstream-sync-golden.json"
DEFAULT_UPSTREAM_REMOTE = "upstream"
DEFAULT_UPSTREAM_REF = "python-semantic-release:master"

STATUS_SAME = "same"
STATUS_CHANGED = "changed"
STATUS_FORK_ONLY = "fork-only"
STATUS_UPSTREAM_ONLY = "upstream-only"

TreeFetcher = Callable[..., tuple[str, dict[str, str]]]

_MODULE_STATUSES = frozenset(
    {STATUS_SAME, STATUS_CHANGED, STATUS_FORK_ONLY, STATUS_UPSTREAM_ONLY}
)


@dataclass(frozen=True)
class ModuleDivergence:
    """One stock file's divergence state against the tracked upstream ref."""

    path: str
    status: str
    upstream_sha: str
    fork_sha: str


@dataclass(frozen=True)
class SyncReport:
    """Full inventory for one upstream ref; JSON-serializable as-is."""

    upstream_ref: str
    manifest_baseline: str
    modules: tuple[ModuleDivergence, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "upstream_ref": self.upstream_ref,
            "manifest_baseline": self.manifest_baseline,
            "modules": [
                {
                    "path": module.path,
                    "status": module.status,
                    "upstream_sha": module.upstream_sha,
                    "fork_sha": module.fork_sha,
                }
                for module in self.modules
            ],
        }


def fetch_upstream_tree(
    repo_dir: Path,
    *,
    remote: str,
    ref: str,
) -> tuple[str, dict[str, str]]:
    """
    Fetch ``ref`` from ``remote`` and return ``(resolved_sha, path→blob_sha)``.

    ``ref`` may be ``owner:ref`` shorthand (remote URL comes from the git
    remote ``upstream``). The real network seam -- tests inject trees
    instead of calling this.
    """
    target = ref
    if ":" in ref:  # "owner:ref" -- resolve through the remote's URL namespace
        owner, _, real_ref = ref.rpartition(":")
        target = f"{remote}/{owner}-{real_ref.replace('/', '-')}"
        subprocess.run(
            ["git", "fetch", "--no-tags", remote, f"{real_ref}:{target}"],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "fetch", "--no-tags", remote, ref],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
        )
        target = f"{remote}/{ref}"
    resolved = subprocess.run(
        ["git", "rev-parse", target],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    listing = subprocess.run(
        ["git", "ls-tree", "-r", target],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tree: dict[str, str] = {}
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) >= 3 and fields[1] == "blob":
            tree[path] = fields[2]
    return resolved, tree


def compare_modules(
    upstream_ref: str,
    manifest_baseline: str,
    upstream_tree: Mapping[str, str],
    fork_tree: Mapping[str, str],
    fork_owned: Mapping[str, str],
) -> SyncReport:
    """
    Pure diff of two path→blob_sha trees over the stock (upstream-tracked)
    surface. ``fork_owned`` maps fork-owned paths (bsr-owned, sync-immune)
    to their fork sha; they are reported as ``fork-only`` and excluded from
    sync candidates.
    """
    modules: list[ModuleDivergence] = []
    stock_paths = sorted(set(upstream_tree) | set(fork_tree) | set(fork_owned))
    for path in stock_paths:
        if path in fork_owned:
            modules.append(
                ModuleDivergence(
                    path=path,
                    status=STATUS_FORK_ONLY,
                    upstream_sha="",
                    fork_sha=fork_owned[path],
                )
            )
            continue
        upstream_sha = upstream_tree.get(path, "")
        fork_sha = fork_tree.get(path, "")
        if upstream_sha and fork_sha:
            status = STATUS_SAME if upstream_sha == fork_sha else STATUS_CHANGED
        elif upstream_sha:
            status = STATUS_UPSTREAM_ONLY
        else:
            status = STATUS_FORK_ONLY
        modules.append(
            ModuleDivergence(
                path=path,
                status=status,
                upstream_sha=upstream_sha,
                fork_sha=fork_sha,
            )
        )
    return SyncReport(
        upstream_ref=upstream_ref,
        manifest_baseline=manifest_baseline,
        modules=tuple(modules),
    )


def canonical_golden_bytes(report: SyncReport) -> bytes:
    """Deterministic serialization: sorted keys, no timestamps, LF."""
    return (
        json.dumps(
            report.to_json_dict(), indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )


def refresh_golden(golden_path: Path, report: SyncReport) -> bytes:
    """Write the golden and return the exact bytes written (idempotent)."""
    payload = canonical_golden_bytes(report)
    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_bytes(payload)
    return payload


def check_against_golden(golden_path: Path, report: SyncReport) -> tuple[bool, str]:
    """Recompute-vs-golden compare; returns (ok, human diff)."""
    if not golden_path.is_file():
        return False, f"golden missing: {golden_path} (run --refresh to pin)"
    expected = canonical_golden_bytes(report)
    actual = golden_path.read_bytes()
    if expected == actual:
        return True, "upstream sync golden is current"
    expected_report = json.loads(expected.decode("utf-8"))
    actual_report = json.loads(actual.decode("utf-8"))
    lines = ["upstream sync golden is STALE:"]
    old = {m["path"]: m["status"] for m in actual_report["modules"]}
    new = {m["path"]: m["status"] for m in expected_report["modules"]}
    lines.extend(
        f"  {path}: {old.get(path, '(absent)')} -> {new.get(path, '(absent)')}"
        for path in sorted(set(old) | set(new))
        if old.get(path) != new.get(path)
    )
    if actual_report.get("upstream_ref") != expected_report.get("upstream_ref"):
        lines.append(
            f"  upstream_ref: {actual_report.get('upstream_ref')} -> {expected_report.get('upstream_ref')}"
        )
    return False, "\n".join(lines)


def _fork_tree_from_git(repo_dir: Path, ref: str) -> tuple[str, dict[str, str]]:
    resolved = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    listing = subprocess.run(
        ["git", "ls-tree", "-r", ref],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tree: dict[str, str] = {}
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) >= 3 and fields[1] == "blob":
            tree[path] = fields[2]
    return resolved, tree


def build_report(
    repo_dir: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    remote: str = DEFAULT_UPSTREAM_REMOTE,
    ref: str = DEFAULT_UPSTREAM_REF,
    fetch_tree: TreeFetcher = fetch_upstream_tree,
    fork_tree_fetcher: TreeFetcher = _fork_tree_from_git,
) -> SyncReport:
    """Assemble the full report (network + fork-tree seams injectable)."""
    manifest = load_manifest(manifest_path)
    _, fork_tree = fork_tree_fetcher(repo_dir, "HEAD")
    fork_owned: dict[str, str] = {
        entry.path: fork_tree[entry.path]
        for entry in manifest.entries
        if entry.path.startswith("src/semantic_release/bsr/")
        and entry.path in fork_tree
    }
    upstream_resolved, upstream_tree = fetch_tree(repo_dir, remote=remote, ref=ref)
    fork_resolved, _ = fork_tree_fetcher(repo_dir, "HEAD")
    return compare_modules(
        upstream_resolved or ref,
        manifest.baseline,
        upstream_tree,
        fork_tree,
        fork_owned,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--remote", default=DEFAULT_UPSTREAM_REMOTE)
    parser.add_argument("--ref", default=DEFAULT_UPSTREAM_REF)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--refresh", action="store_true", help="re-pin the golden")
    parser.add_argument("--check", action="store_true", help="fail on stale golden")
    parser.add_argument("--json", action="store_true", help="print report JSON")
    args = parser.parse_args(argv)

    report = build_report(
        args.repo, manifest_path=args.manifest, remote=args.remote, ref=args.ref
    )
    if args.json:
        print(json.dumps(report.to_json_dict(), indent=2, sort_keys=True))
    changed = sum(1 for m in report.modules if m.status == STATUS_CHANGED)
    print(
        f"upstream {report.upstream_ref}: {len(report.modules)} stock paths, "
        f"{changed} diverged, "
        f"{sum(1 for m in report.modules if m.status == STATUS_FORK_ONLY)} fork-owned"
    )
    if args.refresh:
        payload = refresh_golden(args.golden, report)
        print(f"golden refreshed: {args.golden} ({len(payload)} bytes)")
        return 0
    ok, diff = check_against_golden(args.golden, report)
    print(diff)
    if args.check and not ok:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
