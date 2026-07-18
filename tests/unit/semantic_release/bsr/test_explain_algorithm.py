from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.bsr.explain import BumpStats
from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.enums import LevelBump
from semantic_release.version.algorithm import next_version
from semantic_release.version.translator import VersionTranslator

if TYPE_CHECKING:
    from typing import Mapping

    from git.objects.commit import Commit

    from semantic_release.version.version import Version

_AUTHOR = Actor("t", "t@t")


def _commit(repo: Repo, relpath: str, content: str, message: str) -> Commit:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _next_version(repo: Repo, **kwargs: object) -> object:
    return next_version(
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        allow_zero_version=True,
        major_on_zero=True,
        **kwargs,  # type: ignore[arg-type]
    )


def _capturing_sink(
    captured: list[BumpStats],
) -> object:
    """
    Mirror version.py's real wiring: `next_version()`'s `bump_stats_sink` is
    primitive-args-only (decoupled from bsr/), so the bsr-side caller wraps
    it into a `BumpStats` itself.
    """

    def _sink(
        level_bump: LevelBump,
        commit_count: int,
        latest_version: Version,
        type_counts: Mapping[str, int],
    ) -> None:
        captured.append(
            BumpStats(
                level_bump=level_bump,
                commit_count=commit_count,
                latest_version=latest_version,
                type_counts=type_counts,
            )
        )

    return _sink


def test_bump_stats_sink_omitted_matches_explicit_none(tmp_path: Path) -> None:
    """Omitting `bump_stats_sink` behaves identically to passing `None` -- drop-in parity."""
    repo = Repo.init(tmp_path)
    _commit(repo, "a.txt", "a", "feat: initial")
    assert _next_version(repo) == _next_version(repo, bump_stats_sink=None)


def test_bump_stats_sink_fires_for_a_real_bump(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "a.txt", "a", "feat: add a")
    _commit(repo, "b.txt", "b", "fix: fix b")

    captured: list[BumpStats] = []
    result = _next_version(repo, bump_stats_sink=_capturing_sink(captured))

    assert len(captured) == 1
    stats = captured[0]
    assert stats.level_bump == LevelBump.MINOR
    assert stats.commit_count == 2
    # NOTE: ParsedCommit.type is populated from the human-readable *category*
    # (e.g. "features"/"bug fixes"), not the raw "feat"/"fix" token -- see
    # commit_parser/token.py:154's "# TODO: breaking v11, swap back to type".
    assert stats.type_counts == {"features": 1, "bug fixes": 1}
    assert result != stats.latest_version


def test_bump_stats_sink_fires_for_no_release_with_nonzero_commit_count(
    tmp_path: Path,
) -> None:
    """
    Chore-only commits since the last release: `commit_count` reflects the
    RAW commits scanned, but `type_counts` is empty because none qualify --
    this is exactly the NO_QUALIFYING_COMMITS signal `bsr.explain` needs.
    """
    repo = Repo.init(tmp_path)
    _commit(repo, "a.txt", "a", "feat: initial")
    tag_version = _next_version(repo)
    repo.create_tag(f"v{tag_version}")
    _commit(repo, "b.txt", "b", "chore: tidy up")

    captured: list[BumpStats] = []
    result = _next_version(repo, bump_stats_sink=_capturing_sink(captured))

    assert len(captured) == 1
    stats = captured[0]
    assert stats.level_bump == LevelBump.NO_RELEASE
    assert stats.commit_count == 1
    assert stats.type_counts == {}
    assert result == tag_version  # no release: recomputes the prior tag's version


def test_bump_stats_sink_fires_for_zero_commits_since_last_release(
    tmp_path: Path,
) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "a.txt", "a", "feat: initial")
    tag_version = _next_version(repo)
    repo.create_tag(f"v{tag_version}")

    captured: list[BumpStats] = []
    result = _next_version(repo, bump_stats_sink=_capturing_sink(captured))

    assert len(captured) == 1
    stats = captured[0]
    assert stats.commit_count == 0
    assert stats.type_counts == {}
    assert result == tag_version
