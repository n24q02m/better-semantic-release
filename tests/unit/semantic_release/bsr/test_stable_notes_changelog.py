"""
Real git-repo + real PSR changelog templates coverage for `bsr.
stable_notes_aggregate` (C4) -- reproduces the actual "consumed by
prerelease" bug (issue #555: a stable finalize with no brand-new commits
since the last prerelease renders an EMPTY section) against the REAL
`AngularCommitParser` + `ReleaseHistory.from_git_history` + changelog
templates, in both `init` and `update` changelog modes. Mirrors
test_path_filter_changelog.py's pattern for M2. See test_stable_notes.py
for the synthetic scope/dedup unit matrix, and test_stable_notes_cli.py /
test_stable_notes_parity.py for the real `# BSR-PATCH` CLI seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from git import Actor, Repo

from semantic_release.bsr.stable_notes import aggregate_stable_release
from semantic_release.changelog.context import ChangelogMode, make_changelog_context
from semantic_release.changelog.release_history import ReleaseHistory
from semantic_release.cli.changelog_writer import render_default_changelog_file
from semantic_release.cli.config import ChangelogOutputFormat
from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.hvcs import Github
from semantic_release.version.translator import VersionTranslator
from semantic_release.version.version import Version

_AUTHOR = Actor("t", "t@t")
_REMOTE_URL = "https://github.com/example-owner/example-repo.git"
_INSERTION_FLAG = "<!-- version list -->"


def _commit(repo: Repo, relpath: str, content: str, message: str) -> None:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _build_beta_then_stable_repo(tmp_path: Path) -> Repo:
    """
    v0.1.0 (stable) -> two `feat` commits released as v0.2.0-beta.1 and
    v0.2.0-beta.2 -> HEAD stays exactly at beta.2's commit (no brand-new
    commits afterwards) -- the precise shape that leaves a stable finalize's
    OWN `elements` empty when computed via `ReleaseHistory.from_git_history`
    (issue #555).
    """
    repo = Repo.init(tmp_path)
    _commit(repo, "a.txt", "a", "feat: initial")
    repo.create_tag("v0.1.0")
    _commit(repo, "b.txt", "b", "feat: add thing A")
    repo.create_tag("v0.2.0-beta.1")
    _commit(repo, "c.txt", "c", "feat: add thing B")
    repo.create_tag("v0.2.0-beta.2")
    return repo


def _release_history_upto(repo: Repo, new_version: Version | None) -> ReleaseHistory:
    """
    Full `ReleaseHistory` from git tags, optionally also `.release()`-ing
    `new_version` on top (simulating the in-progress stable finalize, before
    its tag actually exists on disk).
    """
    rh = ReleaseHistory.from_git_history(
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
    )
    if new_version is None:
        return rh
    return rh.release(
        new_version,
        tagger=_AUTHOR,
        committer=_AUTHOR,
        tagged_date=datetime.now(timezone.utc),
    )


def _render_init(release_history: ReleaseHistory) -> str:
    return render_default_changelog_file(
        output_format=ChangelogOutputFormat.MARKDOWN,
        changelog_context=make_changelog_context(
            hvcs_client=Github(_REMOTE_URL),
            release_history=release_history,
            mode=ChangelogMode.INIT,
            prev_changelog_file=Path("CHANGELOG.md"),
            insertion_flag="",
            mask_initial_release=False,
        ),
        changelog_style="conventional",
    )


def _render_update(release_history: ReleaseHistory, prev_changelog_file: Path) -> str:
    return render_default_changelog_file(
        output_format=ChangelogOutputFormat.MARKDOWN,
        changelog_context=make_changelog_context(
            hvcs_client=Github(_REMOTE_URL),
            release_history=release_history,
            mode=ChangelogMode.UPDATE,
            prev_changelog_file=prev_changelog_file,
            insertion_flag=_INSERTION_FLAG,
            mask_initial_release=False,
        ),
        changelog_style="conventional",
    )


def _section(rendered: str, version_tag: str) -> str:
    """The text of one `## {version_tag} (...)` section, up to the next heading."""
    marker = f"## {version_tag} ("
    start = rendered.index(marker)
    rest = rendered[start:]
    end = rest.find("\n## ", 1)
    return rest if end == -1 else rest[:end]


# ---------------------------------------------------------------------------
# init mode
# ---------------------------------------------------------------------------


def test_init_mode_stable_section_empty_without_aggregation(tmp_path: Path) -> None:
    """Baseline: reproduces issue #555 against the REAL template pipeline."""
    repo = _build_beta_then_stable_repo(tmp_path)
    v02 = Version.parse("0.2.0")
    release_history = _release_history_upto(repo, v02)

    section = _section(_render_init(release_history), "v0.2.0").lower()

    assert "thing a" not in section
    assert "thing b" not in section


def test_init_mode_stable_section_aggregates_both_beta_commits(tmp_path: Path) -> None:
    repo = _build_beta_then_stable_repo(tmp_path)
    v02 = Version.parse("0.2.0")
    release_history = _release_history_upto(repo, v02)
    release_history.released[v02] = aggregate_stable_release(
        release_history, new_version=v02, scope="line"
    )

    section = _section(_render_init(release_history), "v0.2.0").lower()

    assert "thing a" in section
    assert "thing b" in section


def test_init_mode_aggregated_commits_are_not_duplicated_within_section(
    tmp_path: Path,
) -> None:
    """Each beta commit's description appears exactly ONCE in the aggregated section."""
    repo = _build_beta_then_stable_repo(tmp_path)
    v02 = Version.parse("0.2.0")
    release_history = _release_history_upto(repo, v02)
    release_history.released[v02] = aggregate_stable_release(
        release_history, new_version=v02, scope="line"
    )

    section = _section(_render_init(release_history), "v0.2.0").lower()

    assert section.count("thing a") == 1
    assert section.count("thing b") == 1


def test_init_mode_prior_beta_sections_are_left_intact(tmp_path: Path) -> None:
    """
    Aggregation only ADDS content to the stable section -- it must not
    remove or alter the prerelease sections still present elsewhere in an
    `init`-mode (full-history) render.
    """
    repo = _build_beta_then_stable_repo(tmp_path)
    v02 = Version.parse("0.2.0")
    release_history = _release_history_upto(repo, v02)
    release_history.released[v02] = aggregate_stable_release(
        release_history, new_version=v02, scope="line"
    )

    rendered = _render_init(release_history).lower()

    beta1_section = _section(rendered, "v0.2.0-beta.1").lower()
    beta2_section = _section(rendered, "v0.2.0-beta.2").lower()
    assert "thing a" in beta1_section
    assert "thing b" in beta2_section


# ---------------------------------------------------------------------------
# update mode
# ---------------------------------------------------------------------------


def test_update_mode_inserts_aggregated_stable_section(tmp_path: Path) -> None:
    """
    Simulates the REAL multi-run CI shape: a `CHANGELOG.md` already on disk
    (as if beta.1 and beta.2 were each committed by a prior real run), then
    a fresh `update`-mode render for the stable finalize inserts ONE new
    `## v0.2.0` section containing both prior commits, aggregated.
    """
    repo = _build_beta_then_stable_repo(tmp_path)
    beta_only_history = _release_history_upto(repo, None)
    header, _, body = _render_init(beta_only_history).partition("\n\n")
    prev_changelog_file = tmp_path / "CHANGELOG.md"
    prev_changelog_file.write_text(
        f"{header}\n\n{_INSERTION_FLAG}\n\n{body}", encoding="utf-8"
    )

    v02 = Version.parse("0.2.0")
    release_history = _release_history_upto(repo, v02)
    release_history.released[v02] = aggregate_stable_release(
        release_history, new_version=v02, scope="line"
    )

    rendered = _render_update(release_history, prev_changelog_file)
    section = _section(rendered, "v0.2.0").lower()

    assert "thing a" in section
    assert "thing b" in section
    # the pre-existing beta sections, already "on disk", are untouched
    assert "thing a" in _section(rendered, "v0.2.0-beta.1").lower()
    assert "thing b" in _section(rendered, "v0.2.0-beta.2").lower()


def test_update_mode_stable_section_empty_without_aggregation(tmp_path: Path) -> None:
    """Parity baseline for the test above: without aggregation, same bug as init mode."""
    repo = _build_beta_then_stable_repo(tmp_path)
    beta_only_history = _release_history_upto(repo, None)
    header, _, body = _render_init(beta_only_history).partition("\n\n")
    prev_changelog_file = tmp_path / "CHANGELOG.md"
    prev_changelog_file.write_text(
        f"{header}\n\n{_INSERTION_FLAG}\n\n{body}", encoding="utf-8"
    )

    v02 = Version.parse("0.2.0")
    release_history = _release_history_upto(repo, v02)

    rendered = _render_update(release_history, prev_changelog_file)
    section = _section(rendered, "v0.2.0").lower()

    assert "thing a" not in section
    assert "thing b" not in section


# ---------------------------------------------------------------------------
# since_stable scope, against real history
# ---------------------------------------------------------------------------


def test_since_stable_scope_matches_line_scope_for_a_single_prerelease_track(
    tmp_path: Path,
) -> None:
    """
    With only ONE prerelease track (the common case), `since_stable` and
    `line` scope select the identical set of intervening prereleases -- they
    only diverge when a differently-lined prerelease track was abandoned
    (covered by the synthetic scope tests in test_stable_notes.py).
    """
    repo = _build_beta_then_stable_repo(tmp_path)
    v02 = Version.parse("0.2.0")

    line_history = _release_history_upto(repo, v02)
    line_result = aggregate_stable_release(line_history, new_version=v02, scope="line")

    since_stable_history = _release_history_upto(repo, v02)
    since_stable_result = aggregate_stable_release(
        since_stable_history, new_version=v02, scope="since_stable"
    )

    line_shas = {
        c.hexsha for commits in line_result["elements"].values() for c in commits
    }
    since_stable_shas = {
        c.hexsha
        for commits in since_stable_result["elements"].values()
        for c in commits
    }
    assert line_shas == since_stable_shas
    assert len(line_shas) == 2
