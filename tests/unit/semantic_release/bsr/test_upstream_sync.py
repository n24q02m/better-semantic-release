"""Unit tests for the upstream sync harness (wave 2, W2.4)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from scripts.upstream_sync import SyncReport
from scripts.upstream_sync import (
    STATUS_CHANGED,
    STATUS_FORK_ONLY,
    STATUS_SAME,
    STATUS_UPSTREAM_ONLY,
    ModuleDivergence,
    build_report,
    canonical_golden_bytes,
    check_against_golden,
    compare_modules,
    refresh_golden,
)

UPSTREAM = {
    "src/semantic_release/stock_a.py": "sha-up-a1",
    "src/semantic_release/stock_b.py": "sha-up-b1",
    "docs/index.rst": "sha-up-doc1",
}
FORK = {
    "src/semantic_release/stock_a.py": "sha-up-a1",  # same as upstream
    "src/semantic_release/stock_b.py": "sha-fork-b1",  # diverged
    "docs/index.rst": "sha-up-doc1",
    "src/semantic_release/bsr/gating.py": "sha-fork-owned",
}
FORK_OWNED = {"src/semantic_release/bsr/gating.py": "sha-fork-owned"}


def _report() -> SyncReport:
    return compare_modules("up/main", "baseline-1", UPSTREAM, FORK, FORK_OWNED)


class TestCompare:
    def test_statuses(self) -> None:
        by_path = {m.path: m.status for m in _report().modules}
        assert by_path["src/semantic_release/stock_a.py"] == STATUS_SAME
        assert by_path["src/semantic_release/stock_b.py"] == STATUS_CHANGED
        assert by_path["src/semantic_release/bsr/gating.py"] == STATUS_FORK_ONLY
        assert by_path["docs/index.rst"] == STATUS_SAME

    def test_upstream_only_and_fork_only_deletions(self) -> None:
        report = compare_modules(
            "r",
            "b",
            {"kept.py": "s1", "deleted-locally.py": "s2"},
            {"kept.py": "s1", "added-locally.py": "f1"},
            {},
        )
        by_path = {m.path: m.status for m in report.modules}
        assert by_path["deleted-locally.py"] == STATUS_UPSTREAM_ONLY
        assert by_path["added-locally.py"] == STATUS_FORK_ONLY

    def test_report_carries_refs_and_baseline(self) -> None:
        report = _report()
        assert report.upstream_ref == "up/main"
        assert report.manifest_baseline == "baseline-1"

    def test_fork_owned_shas_recorded(self) -> None:
        module = next(
            m
            for m in _report().modules
            if m.path == "src/semantic_release/bsr/gating.py"
        )
        assert module.fork_sha == "sha-fork-owned"
        assert module.upstream_sha == ""


class TestGolden:
    def test_canonical_bytes_are_deterministic(self) -> None:
        assert canonical_golden_bytes(_report()) == canonical_golden_bytes(_report())

    def test_refresh_is_idempotent(self, tmp_path: Path) -> None:
        golden = tmp_path / "golden.json"
        first = refresh_golden(golden, _report())
        second = refresh_golden(golden, _report())
        assert first == second
        assert golden.read_bytes() == first

    def test_check_passes_when_current(self, tmp_path: Path) -> None:
        golden = tmp_path / "golden.json"
        refresh_golden(golden, _report())
        ok, message = check_against_golden(golden, _report())
        assert ok
        assert "current" in message

    def test_check_detects_upstream_move(self, tmp_path: Path) -> None:
        golden = tmp_path / "golden.json"
        refresh_golden(golden, _report())
        moved_upstream = dict(UPSTREAM)
        moved_upstream["src/semantic_release/stock_a.py"] = "sha-up-a2"
        drifted = compare_modules(
            "up/main", "baseline-1", moved_upstream, FORK, FORK_OWNED
        )
        ok, message = check_against_golden(golden, drifted)
        assert not ok
        assert "STALE" in message
        assert "stock_a.py" in message

    def test_check_detects_local_drift(self, tmp_path: Path) -> None:
        golden = tmp_path / "golden.json"
        refresh_golden(golden, _report())
        drifted_fork = dict(FORK)
        drifted_fork["docs/index.rst"] = "sha-fork-doc1"
        drifted = compare_modules(
            "up/main", "baseline-1", UPSTREAM, drifted_fork, FORK_OWNED
        )
        ok, _ = check_against_golden(golden, drifted)
        assert not ok

    def test_check_without_golden_fails_closed(self, tmp_path: Path) -> None:
        ok, message = check_against_golden(tmp_path / "ghost.json", _report())
        assert not ok
        assert "missing" in message

    def test_golden_json_shape(self, tmp_path: Path) -> None:
        golden = tmp_path / "golden.json"
        refresh_golden(golden, _report())
        doc = json.loads(golden.read_text(encoding="utf-8"))
        assert doc["upstream_ref"] == "up/main"
        assert doc["manifest_baseline"] == "baseline-1"
        assert {m["status"] for m in doc["modules"]} <= {
            STATUS_SAME,
            STATUS_CHANGED,
            STATUS_FORK_ONLY,
            STATUS_UPSTREAM_ONLY,
        }
        assert not any("generated" in key or "at" in key for key in doc)


class TestBuildReport:
    def test_network_seam_injection(self, tmp_path: Path) -> None:
        """build_report never touches git when the fetch seam is injected."""
        manifest = tmp_path / "ownership.toml"
        manifest.write_text(
            'baseline = "test-baseline"\n'
            '\n[[ownership]]\npath = "src/semantic_release/bsr/x.py"\n'
            'status = "A"\nkind = "python-source"\nowner = "bsr"\n'
            'reason = "fork-owned"\ngates = ["python-lint"]\n',
            encoding="utf-8",
        )
        upstream_tree = {
            "src/semantic_release/stock.py": "up1",
            "src/semantic_release/bsr/x.py": "would-be-clobbered",
        }

        def fake_fetch(repo_dir, *, remote, ref):
            assert remote == "up"
            assert ref == "main"
            return "resolved-sha", upstream_tree

        def fake_fork_tree(repo_dir, ref):
            return "fork-sha", {
                "src/semantic_release/bsr/x.py": "fork-owned-sha",
            }

        report = build_report(
            tmp_path,
            manifest_path=manifest,
            remote="up",
            ref="main",
            fetch_tree=fake_fetch,
            fork_tree_fetcher=fake_fork_tree,
        )
        assert report.upstream_ref == "resolved-sha"
        assert report.manifest_baseline == "test-baseline"
        by_path = {m.path: m.status for m in report.modules}
        assert by_path["src/semantic_release/stock.py"] == STATUS_UPSTREAM_ONLY
        assert by_path["src/semantic_release/bsr/x.py"] == STATUS_FORK_ONLY


class TestDataclasses:
    def test_divergence_frozen(self) -> None:
        module = ModuleDivergence("p", STATUS_SAME, "u", "f")
        with pytest.raises(Exception):  # noqa: B017, PT011 - frozen dataclass
            module.path = "other"  # type: ignore[misc]
