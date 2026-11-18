from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from semantic_release.cli.commands.version import (
    is_forced_prerelease,
    version_from_forced_level,
)
from semantic_release.enums import LevelBump
from semantic_release.version.translator import VersionTranslator

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "force_prerelease, force_level, prerelease, expected",
    [
        *[
            (True, force_level, prerelease, True)
            for force_level in (None, "major", "minor", "patch")
            for prerelease in (True, False)
        ],
        *[
            (False, force_level, prerelease, False)
            for force_level in ("major", "minor", "patch")
            for prerelease in (True, False)
        ],
        *[(False, None, prerelease, prerelease) for prerelease in (True, False)],
    ],
)
def test_is_forced_prerelease(force_prerelease, force_level, prerelease, expected):
    assert is_forced_prerelease(force_prerelease, force_level, prerelease) == expected


def _repo_with_tags(repo_dir: Path, tags: list[str]) -> None:
    with Repo.init(repo_dir) as git_repo:
        with git_repo.config_writer() as config:
            config.set_value("user", "name", "Test")
            config.set_value("user", "email", "test@example.com")
        git_repo.index.commit("init")
        for tag in tags:
            git_repo.create_tag(tag)


@pytest.mark.parametrize(
    "tags, expected",
    [
        # A final release that followed its own prereleases: the next forced
        # prerelease must target the next patch, not reuse 1.13.1's suffix
        # (1.13.1-beta.4 sorts below 1.13.1 and would downgrade --pre users).
        (
            [
                "v1.13.0",
                "v1.13.1-beta.1",
                "v1.13.1-beta.2",
                "v1.13.1-beta.3",
                "v1.13.1",
            ],
            "1.13.2-beta.1",
        ),
        # A final release with no prereleases before it: same rule.
        (["v1.13.0", "v1.13.1"], "1.13.2-beta.1"),
        # The latest tag is already a prerelease: keep counting its revision.
        (["v1.13.1", "v1.13.2-beta.1"], "1.13.2-beta.2"),
    ],
)
def test_forced_prerelease_sorts_above_the_latest_release(
    tmp_path: Path, tags: list[str], expected: str
):
    _repo_with_tags(tmp_path, tags)

    version = version_from_forced_level(
        repo_dir=tmp_path,
        forced_level_bump=LevelBump.PRERELEASE_REVISION,
        translator=VersionTranslator(tag_format="v{version}", prerelease_token="beta"),
    )

    assert str(version) == expected
