"""
The license in release notes comes from `pyproject.toml` as it was at the
release tag (`git show <tag>:pyproject.toml`).

A file committed with CRLF line endings has to parse too. GitPython strips the
final newline of `git show` output by default, which leaves a lone carriage
return at the end of the text, and tomlkit rejects a lone carriage return.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from semantic_release.cli.commands.changelog import get_license_name_for_release

if TYPE_CHECKING:
    from pathlib import Path

_AUTHOR = Actor("demo", "demo@example.com")
_DATE = "2024-01-15T12:00:00 +0000"
_PYPROJECT_LINES = ["[project]", 'name = "demo"', 'license = "MIT"', ""]


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_license_is_read_from_the_pyproject_at_the_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, newline: str
) -> None:
    project_root = tmp_path.resolve()
    repo = Repo.init(project_root, initial_branch="main")
    pyproject_bytes = newline.join(_PYPROJECT_LINES).encode("utf-8")
    (project_root / "pyproject.toml").write_bytes(pyproject_bytes)
    repo.index.add(["pyproject.toml"])
    repo.index.commit(
        "feat: initial",
        author=_AUTHOR,
        committer=_AUTHOR,
        author_date=_DATE,
        commit_date=_DATE,
    )
    repo.create_tag("v1.0.0")
    # The blob keeps the line endings it was written with.
    assert repo.tree("v1.0.0")["pyproject.toml"].data_stream.read() == pyproject_bytes
    monkeypatch.chdir(project_root)

    assert get_license_name_for_release("v1.0.0", project_root) == "MIT"
