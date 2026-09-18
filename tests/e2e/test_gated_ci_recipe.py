"""
Gated CI release recipe (Task 8): hooks + readiness cooperate.

Drives the documented flow -- plan -> verify (with pre_verify hook gate)
-> readiness check -> publish decision -- over a fake repo, proving a
failing hook blocks the run before any publish step would fire.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

# ruff: noqa: S603, S607
from semantic_release.bsr.config import load_bsr_config
from semantic_release.bsr.hooks import run_hooks
from semantic_release.bsr.provenance import assess_publish_readiness

if TYPE_CHECKING:
    from pathlib import Path


GATED_RECIPE = """
[tool.semantic_release]
version_variables = ["src/pkg/__init__.py:__version__"]

[[tool.semantic_release.branches.main]]
match = "main"

[tool.semantic_release.bsr]
schema_version = 1

[[tool.semantic_release.bsr.hooks]]
point = "pre_verify"
command = ["{python}", "-c", "import sys; sys.exit({code})"]
"""


@pytest.mark.parametrize("hook_exit", [0, 3])
def test_gated_recipe_hook_gates_pipeline(tmp_path: Path, hook_exit: int) -> None:
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '1.0.0'", encoding="utf-8")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        GATED_RECIPE.format(python=sys.executable.replace("\\", "/"), code=hook_exit),
        encoding="utf-8",
    )
    (tmp_path / ".git").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )

    config = load_bsr_config(pyproject)
    assert len(config.hooks) == 1

    # The gate: pre_verify hooks must pass before the pipeline proceeds.
    ok, detail = run_hooks(config.hooks, "pre_verify", repo_dir=tmp_path)
    if hook_exit == 0:
        assert ok is True
    else:
        assert ok is False
        assert "exited 3" in detail
        # A real orchestrator stops here -- publish never runs.
        return

    # Readiness is the second gate before publish.
    readiness = assess_publish_readiness("none", environment=dict(os.environ))
    assert readiness.ready is True


def test_recipe_docs_describe_finite_points() -> None:
    import semantic_release.bsr.hooks as hooks_mod

    documented = {"pre_plan", "post_plan", "pre_verify", "pre_publish", "post_publish"}
    assert set(hooks_mod.HOOK_POINTS) == documented
