from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from semantic_release.bsr import guards as g
from semantic_release.bsr.config import BsrConfig
from semantic_release.bsr.errors import BsrGuardError
from semantic_release.bsr.registry import ProbeResult
from semantic_release.version.translator import VersionTranslator

if TYPE_CHECKING:
    from pathlib import Path


def _runtime(tmp_path: Path, name: str = "my-pkg") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        repo_dir=tmp_path,
        version_translator=VersionTranslator(tag_format="v{version}"),
        project_metadata={"name": name},
    )


def _repo_with_head(tmp_path: Path) -> Repo:
    repo = Repo.init(tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    repo.index.add(["f.txt"])
    repo.index.commit("feat: a", author=Actor("t", "t@t"), committer=Actor("t", "t@t"))
    return repo


# --- resolve_registry ---
def test_resolve_explicit_wins():
    assert g.resolve_registry(BsrConfig(registry="npm"), "x") == "npm"


def test_resolve_empty_autodetects_pypi_from_name():
    assert g.resolve_registry(BsrConfig(registry=""), "x") == "pypi"


def test_resolve_empty_no_name_is_none():
    assert g.resolve_registry(BsrConfig(registry=""), "") == "none"


def test_resolve_unknown_raises():
    with pytest.raises(BsrGuardError):
        g.resolve_registry(BsrConfig(registry="cargo"), "x")


# --- run_guards: registry ---
def test_collision_raises(tmp_path, monkeypatch):
    _repo_with_head(tmp_path)
    monkeypatch.setattr(g, "probe_registry", lambda *_a, **_k: ProbeResult.EXISTS)
    with pytest.raises(BsrGuardError) as exc:
        g.run_guards(runtime=_runtime(tmp_path), new_version="1.2.3", bsr_config=BsrConfig())
    assert "1.2.3" in exc.value.message


def test_unknown_is_fail_closed(tmp_path, monkeypatch):
    _repo_with_head(tmp_path)
    monkeypatch.setattr(g, "probe_registry", lambda *_a, **_k: ProbeResult.UNKNOWN)
    with pytest.raises(BsrGuardError):
        g.run_guards(runtime=_runtime(tmp_path), new_version="1.2.3", bsr_config=BsrConfig())


def test_free_passes(tmp_path, monkeypatch):
    _repo_with_head(tmp_path)
    monkeypatch.setattr(g, "probe_registry", lambda *_a, **_k: ProbeResult.FREE)
    g.run_guards(runtime=_runtime(tmp_path), new_version="1.2.3", bsr_config=BsrConfig())  # no raise


def test_registry_none_skips_probe(tmp_path, monkeypatch):
    _repo_with_head(tmp_path)
    called = {"n": 0}
    monkeypatch.setattr(
        g, "probe_registry", lambda *_a, **_k: called.__setitem__("n", called["n"] + 1)
    )
    g.run_guards(
        runtime=_runtime(tmp_path, name=""),
        new_version="1.2.3",
        bsr_config=BsrConfig(registry="none"),
    )
    assert called["n"] == 0


def test_collision_guard_disabled(tmp_path, monkeypatch):
    _repo_with_head(tmp_path)
    monkeypatch.setattr(g, "probe_registry", lambda *_a, **_k: ProbeResult.EXISTS)
    g.run_guards(
        runtime=_runtime(tmp_path),
        new_version="1.2.3",
        bsr_config=BsrConfig(guard_registry_collision=False),
    )  # disabled -> no raise despite EXISTS
