from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path


def _build_minimal_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_pyproject: str = ""
) -> Path:
    """
    Build a tiny repo PSR can run `version` on far enough to reach the guard
    seam, optionally with extra `pyproject.toml` content (e.g. a `[tool.
    semantic_release.bsr]` table) baked in from the initial commit.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    extra_pyproject = extra_pyproject.replace(
        "\n[tool.semantic_release.bsr]\n",
        "\n[tool.semantic_release.bsr]\nschema_version = 1\n",
        1,
    )
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "demo-pkg"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
        'tag_format = "v{version}"\n' + extra_pyproject,
        encoding="utf-8",
    )
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml"])
    repo.index.commit(
        "feat: initial", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
    )
    # PSR resolves the hvcs client from the `origin` remote URL; without one,
    # `git remote get-url origin` fails before the command reaches the guard seam.
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    monkeypatch.chdir(proj)
    return proj


@pytest.fixture
def minimal_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tiny repo PSR can run `version` on far enough to reach the guard seam."""
    return _build_minimal_project(tmp_path, monkeypatch)


def test_guard_trip_exits_1(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(**kwargs: object) -> None:
        raise BsrGuardError("BSR-BOOM-orphan")

    monkeypatch.setattr("semantic_release.cli.commands.version.run_guards", _boom)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 1
    assert "BSR-BOOM-orphan" in result.output


def test_guard_pass_proceeds(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    monkeypatch.setattr(
        "semantic_release.cli.commands.version.run_guards",
        lambda **_kwargs: calls.__setitem__("n", calls["n"] + 1),
    )
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert calls["n"] == 1  # hook was reached exactly once


def test_guard_trip_writes_no_github_output(
    minimal_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A `run_guards()` trip must write NOTHING to `$GITHUB_OUTPUT`, even though
    the close-callback is registered long before the guard hook runs -- a
    blocked release must not leak a misleading `released=false` plus
    version/tag that a downstream workflow step could act on.
    """

    def _boom(**kwargs: object) -> None:
        raise BsrGuardError("BSR-BOOM-orphan")

    monkeypatch.setattr("semantic_release.cli.commands.version.run_guards", _boom)
    output_file = tmp_path / "gha.out"
    result = CliRunner(mix_stderr=True).invoke(
        main,
        ["--noop", "version", "--no-commit", "--no-tag", "--no-push"],
        env={"GITHUB_OUTPUT": str(output_file)},
    )
    assert result.exit_code == 1
    assert not output_file.exists()


def _tag_already_computed_version(proj: Path) -> None:
    """
    Force PSR's `next_version` to collide with an existing tag (the orphan-tag /
    rewritten-history silent-freeze precondition): run `version --print` (no side
    effects) to learn what PSR would compute from `minimal_project`'s single
    `feat: initial` commit with no prior tags, then tag HEAD with exactly that
    version. The next real `version` invocation will then find `new_version` in
    `previously_released_versions`.
    """
    repo = Repo(str(proj))
    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    computed_version = printed.output.strip().splitlines()[-1]
    repo.create_tag(f"v{computed_version}")


def _build_orphaned_tag_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_pyproject: str = ""
) -> Path:
    """
    Build a repo with a genuine orphan/rewritten-tag silent-freeze.

    Sequence: c1 ("feat: initial") gets tagged with whatever PSR computes for
    it (e.g. v1.0.0). c2, a second feat commit on top of c1, gets tagged with
    PSR's next computed version (e.g. v1.1.0). Then, simulating a rebase /
    force-push that dropped the `chore(release)` commit, the branch is reset
    back to c1 and a diverging feat commit c3 is added on top -- orphaning the
    v1.1.0 tag (it still exists, but is no longer reachable from HEAD). PSR
    then recomputes, from the still-reachable v1.0.0 base plus the new feat
    commit, the same version already consumed by the now-unreachable tag.
    """
    proj = _build_minimal_project(
        tmp_path, monkeypatch, extra_pyproject=extra_pyproject
    )
    repo = Repo(str(proj))

    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    v1 = printed.output.strip().splitlines()[-1]
    repo.create_tag(f"v{v1}")
    c1 = repo.head.commit

    (proj / "feature2.txt").write_text("feature 2\n", encoding="utf-8")
    repo.index.add(["feature2.txt"])
    repo.index.commit(
        "feat: add thing 2", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
    )
    printed = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert printed.exit_code == 0
    v2 = printed.output.strip().splitlines()[-1]
    assert v2 != v1
    repo.create_tag(f"v{v2}")

    # Simulate the rebase/force-push: rewind the branch to c1 and add a
    # diverging feat commit, orphaning the v2 tag.
    repo.git.reset("--hard", c1.hexsha)
    (proj / "feature3.txt").write_text("feature 3\n", encoding="utf-8")
    repo.index.add(["feature3.txt"])
    repo.index.commit(
        "feat: add thing 3", author=Actor("t", "t@t"), committer=Actor("t", "t@t")
    )
    assert not repo.is_ancestor(repo.tags[f"v{v2}"].commit, repo.head.commit)
    return proj


def test_orphan_recompute_escalates_to_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The real orphan/rewritten-tag silent-freeze: PSR recomputes a version that
    already exists as an ORPHANED (unreachable-from-HEAD) tag because a
    rebase/force-push dropped the commit that originally earned it. With
    `guard_orphan_tag` enabled (the default), this must fail loud.
    """
    _build_orphaned_tag_project(tmp_path, monkeypatch)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 1
    assert "SILENT RELEASE FREEZE PREVENTED" in result.output
    assert "already been released" in result.output


def test_benign_noop_stays_silent(minimal_project: Path) -> None:
    """
    A benign no-op re-dispatch: no new releasable commits, so PSR recomputes
    `new_version` equal to the tag already on (and reachable from) HEAD --
    nothing is orphaned or unreachable. Even with `guard_orphan_tag` enabled
    (the default), this must stay silent like stock PSR, proving the guard
    does not cry-wolf on a benign no-op.
    """
    _tag_already_computed_version(minimal_project)
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "SILENT RELEASE FREEZE PREVENTED" not in result.output
    assert "already been released" in result.output


def test_orphan_guard_trip_writes_no_github_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The orphan/rewritten-tag silent-freeze guard trip must also write
    NOTHING to `$GITHUB_OUTPUT` -- it is a release-safety guard exit just
    like `run_guards()`, and the fix must cover both trip points.
    """
    proj = _build_orphaned_tag_project(tmp_path, monkeypatch)
    output_file = proj.parent / "gha.out"
    result = CliRunner(mix_stderr=True).invoke(
        main,
        ["--noop", "version", "--no-commit", "--no-tag", "--no-push"],
        env={"GITHUB_OUTPUT": str(output_file)},
    )
    assert result.exit_code == 1
    assert "SILENT RELEASE FREEZE PREVENTED" in result.output
    assert not output_file.exists()


def test_benign_noop_still_writes_github_output(minimal_project: Path) -> None:
    """
    A benign no-op (not a guard trip) must keep writing `$GITHUB_OUTPUT` as
    before -- this is stock PSR behavior predating the BSR guards, and the
    guard-trip output fix must not regress it.
    """
    _tag_already_computed_version(minimal_project)
    output_file = minimal_project.parent / "gha.out"
    result = CliRunner(mix_stderr=True).invoke(
        main,
        ["--noop", "version", "--no-commit", "--no-tag", "--no-push"],
        env={"GITHUB_OUTPUT": str(output_file)},
    )
    assert result.exit_code == 0
    assert output_file.exists()
    assert "released=false" in output_file.read_text(encoding="utf-8")


def test_silent_freeze_opt_out_stays_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With `guard_orphan_tag = false`, even a genuine orphan/rewritten-tag
    silent-freeze -- which WOULD otherwise fire, see
    `test_orphan_recompute_escalates_to_exit_1` -- keeps PSR's original
    silent, exit-0 behavior, proving the opt-out.
    """
    _build_orphaned_tag_project(
        tmp_path,
        monkeypatch,
        extra_pyproject="\n[tool.semantic_release.bsr]\nguard_orphan_tag = false\n",
    )
    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0
    assert "SILENT RELEASE FREEZE PREVENTED" not in result.output
    assert "already been released" in result.output
