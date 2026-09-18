"""
Recipe fixture validation (Task 9).

Every TOML config block embedded in ``docs/recipes/*.rst`` must parse through
the real ``load_bsr_config`` -- a docs edit that breaks a recipe fails the
unit suite, keeping the adoption surface honest.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from semantic_release.bsr.config import load_bsr_config

DOCS_RECIPES = Path(__file__).resolve().parents[4] / "docs" / "recipes"

# rst code blocks that are full pyproject-style configs (not shell snippets)
_TOML_BLOCK = re.compile(r"\.\. code-block:: toml\n\n((?:[ \t]+.*\n|\n)+)")


def _recipe_files() -> list[Path]:
    assert DOCS_RECIPES.is_dir(), "docs/recipes missing"
    return sorted(DOCS_RECIPES.glob("*.rst"))


def _toml_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    blocks = []
    for match in _TOML_BLOCK.finditer(text):
        raw = match.group(1)
        block = textwrap.dedent(raw)
        # skip blocks that are partial (continuation snippets start with a
        # table array header and are valid on their own)
        blocks.append(block)
    return blocks


@pytest.mark.parametrize(
    "recipe",
    [
        "python_package.rst",
        "node_package.rst",
        "rust_crate.rst",
        "generic_repo.rst",
    ],
)
def test_recipe_toml_blocks_parse(recipe: str, tmp_path: Path) -> None:
    path = DOCS_RECIPES / recipe
    blocks = _toml_blocks(path)
    assert blocks, f"no toml blocks found in {recipe}"

    for index, block in enumerate(blocks):
        pyproject = tmp_path / f"pyproject-{recipe}-{index}.toml"
        pyproject.write_text(block, encoding="utf-8")
        config = load_bsr_config(pyproject)
        # only complete configs declare the bare bsr table; continuation
        # fragments (e.g. a second publisher snippet) must still parse
        if "[tool.semantic_release.bsr]" in block.split():
            assert config.schema_version == 1


def test_recipes_index_links_all_recipes() -> None:
    index_text = (DOCS_RECIPES / "index.rst").read_text(encoding="utf-8")
    for recipe in _recipe_files():
        if recipe.name == "index.rst":
            continue
        stem = recipe.stem
        assert stem in index_text, f"{stem} missing from recipes index"


def test_migration_doc_keeps_upstream_compatibility_claim() -> None:
    text = (DOCS_RECIPES / "migration.rst").read_text(encoding="utf-8")
    assert "keep working" in text
    assert "schema_version = 1" in text


def test_maintenance_doc_names_the_two_zones() -> None:
    text = (DOCS_RECIPES / "maintenance.rst").read_text(encoding="utf-8")
    assert "Upstream-owned" in text
    assert "BSR-owned" in text
    assert "bsr-upstream-ownership.toml" in text
