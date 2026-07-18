from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.bsr.path_filter import filter_commits_by_paths
from semantic_release.changelog.context import ChangelogMode, make_changelog_context
from semantic_release.changelog.release_history import ReleaseHistory
from semantic_release.cli.changelog_writer import render_default_changelog_file
from semantic_release.cli.config import ChangelogOutputFormat
from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.hvcs import Github
from semantic_release.version.translator import VersionTranslator
from semantic_release.version.version import Version

if TYPE_CHECKING:
    from typing import Callable, Sequence

    from git.objects.commit import Commit

_AUTHOR = Actor("t", "t@t")
_REMOTE_URL = "https://github.com/example-owner/example-repo.git"


def _commit(repo: Repo, relpath: str, content: str, message: str) -> Commit:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _from_git_history(
    repo: Repo,
    commit_path_filter: Callable[[Sequence[Commit]], Sequence[Commit]] | None = None,
) -> ReleaseHistory:
    return ReleaseHistory.from_git_history(
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        commit_path_filter=commit_path_filter,
    )


def _unreleased_feature_hashes(rh: ReleaseHistory) -> set[str]:
    return {parsed.commit.hexsha for parsed in rh.unreleased.get("features", [])}


def test_from_git_history_omitted_filter_matches_explicit_none(tmp_path: Path) -> None:
    """Omitting `commit_path_filter` behaves identically to passing `None` -- drop-in parity."""
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    _commit(repo, "apps/web/y.py", "y", "feat: web")
    assert _unreleased_feature_hashes(_from_git_history(repo)) == (
        _unreleased_feature_hashes(_from_git_history(repo, commit_path_filter=None))
    )


def test_from_git_history_filter_excludes_non_touching_commits(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    c1 = _commit(repo, "apps/api/x.py", "x", "feat: api")
    _commit(repo, "apps/web/y.py", "y", "feat: web")

    rh = _from_git_history(
        repo, commit_path_filter=lambda commits: filter_commits_by_paths(commits, ("apps/api",))
    )
    assert _unreleased_feature_hashes(rh) == {c1.hexsha}


def test_from_git_history_filter_keeping_all_matches_baseline(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: api")
    _commit(repo, "apps/web/y.py", "y", "feat: web")

    baseline = _unreleased_feature_hashes(_from_git_history(repo))
    filtered = _unreleased_feature_hashes(_from_git_history(repo, commit_path_filter=list))
    assert filtered == baseline
    assert len(baseline) == 2


def _render_changelog(release_history: ReleaseHistory) -> str:
    return render_default_changelog_file(
        output_format=ChangelogOutputFormat.MARKDOWN,
        changelog_context=make_changelog_context(
            hvcs_client=Github(_REMOTE_URL),
            release_history=release_history,
            mode=ChangelogMode.INIT,
            prev_changelog_file=Path("CHANGELOG.md"),
            insertion_flag="",
            # NOTE: False, not True -- with only one release in these fixtures'
            # history, that release IS the initial one; masking it would hide
            # the commit descriptions these tests assert on.
            mask_initial_release=False,
        ),
        changelog_style="conventional",
    )


def test_rendered_changelog_excludes_web_only_commit_includes_api_commit(
    tmp_path: Path,
) -> None:
    """
    The RENDERED changelog text (not just the internal `ReleaseHistory`
    structure) for `apps/api`, filtered to `paths=("apps/api",)`, must
    include the description of the commit touching `apps/api` and must NOT
    contain any trace of the commit that only touched `apps/web`.
    """
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: add api health endpoint")
    _commit(repo, "apps/web/y.py", "y", "feat: add web contact form")

    release_history = _from_git_history(
        repo, commit_path_filter=lambda commits: filter_commits_by_paths(commits, ("apps/api",))
    ).release(
        Version.parse("0.1.0"),
        tagger=_AUTHOR,
        committer=_AUTHOR,
        tagged_date=datetime.now(timezone.utc),
    )

    changelog_text = _render_changelog(release_history).lower()

    assert "api health endpoint" in changelog_text
    assert "web contact form" not in changelog_text


def test_rendered_changelog_without_filter_includes_both_commits(tmp_path: Path) -> None:
    """
    Baseline for the test above: with no filter, both commits' descriptions
    are present -- proving the exclusion above is the filter's doing, not an
    artifact of the fixture or the template.
    """
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: add api health endpoint")
    _commit(repo, "apps/web/y.py", "y", "feat: add web contact form")

    release_history = _from_git_history(repo).release(
        Version.parse("0.1.0"),
        tagger=_AUTHOR,
        committer=_AUTHOR,
        tagged_date=datetime.now(timezone.utc),
    )

    changelog_text = _render_changelog(release_history).lower()

    assert "api health endpoint" in changelog_text
    assert "web contact form" in changelog_text
