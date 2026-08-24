from __future__ import annotations

import re
from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.changelog.release_history import ReleaseHistory
from semantic_release.cli.commands import version as version_module
from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("t", "t@t")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _api_pyproject_toml(extra_bsr: str) -> str:
    extra_bsr = extra_bsr.replace(
        "\n[tool.semantic_release.bsr]\n",
        "\n[tool.semantic_release.bsr]\nschema_version = 1\n",
        1,
    )
    if (
        "\n[[tool.semantic_release.bsr.components]]\n" in extra_bsr
        and "schema_version" not in extra_bsr
    ):
        extra_bsr = extra_bsr.replace(
            "\n[[tool.semantic_release.bsr.components]]\n",
            "\n[tool.semantic_release.bsr]\nschema_version = 1\n"
            "[[tool.semantic_release.bsr.components]]\n",
            1,
        )
    return (
        '[project]\nname = "demo-api"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
        'tag_format = "api-v{version}"\n'
        # NOTE: PSR forces a MAJOR bump out of any 0.x.x version whenever
        # `allow_zero_version` is False (the PSR default) -- regardless of
        # whether there were any releasable commits at all. Setting it True
        # here keeps "no commits matched the filter" genuinely meaning "no
        # release" (unchanged version), which is what these tests assert.
        "allow_zero_version = true\n" + extra_bsr
    )


def _build_monorepo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_bsr: str = "",
    second_commit_relpath: str = "apps/web/app.py",
) -> Path:
    """
    Build a 2-component monorepo (`apps/api`, `apps/web`) already released for
    `apps/api` at `api-v0.1.0`, then add ONE new commit touching
    `second_commit_relpath` (defaults to `apps/web`, i.e. NOT `apps/api`).
    Chdir into `apps/api` (mirrors the GH Action's `directory:` input `cd`-ing
    there) so PSR resolves `apps/api/pyproject.toml` as the active config --
    `apps/api` also becomes `os.getcwd()` at CLI invocation time, which is
    what the path-filter default-paths wiring must resolve against (NOT
    `runtime.repo_dir`, which always normalises to `proj`, the git repo root).
    """
    proj = tmp_path / "proj"
    _write(proj / "apps" / "api" / "pyproject.toml", _api_pyproject_toml(extra_bsr))
    _write(proj / "apps" / "api" / "src.py", "print('api')\n")
    repo = Repo.init(proj)
    repo.index.add(["apps/api/pyproject.toml", "apps/api/src.py"])
    repo.index.commit("feat: initial api", author=_AUTHOR, committer=_AUTHOR)
    repo.create_tag("api-v0.1.0")
    # PSR resolves the hvcs client from the `origin` remote URL; without one,
    # `git remote get-url origin` fails before the command reaches `next_version`.
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")

    _write(proj / second_commit_relpath, "print('second commit')\n")
    repo.index.add([second_commit_relpath])
    repo.index.commit("feat: add a feature", author=_AUTHOR, committer=_AUTHOR)

    monkeypatch.chdir(proj / "apps" / "api")
    return proj


def _print_version() -> str:
    """
    Run `version --print` and return the version it prints.

    `--print` always echoes the computed version to stdout, even on the
    "already released" no-bump path (which ALSO writes a warning line to
    stderr afterwards) -- so pick the printed-version line explicitly rather
    than assuming it is the last line of merged output.
    """
    result = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])
    assert result.exit_code == 0, result.output
    return next(
        line
        for line in result.output.strip().splitlines()
        if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+].*)?", line)
    )


def test_path_filter_excludes_web_only_commit_from_api_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With `path_filter=true, paths=["apps/api"]`, a commit touching ONLY
    `apps/web` must not bump `apps/api`'s version -- proves the #168 fix.
    """
    _build_monorepo(
        tmp_path,
        monkeypatch,
        extra_bsr=(
            "\n[tool.semantic_release.bsr]\n"
            "path_filter = true\n"
            'paths = ["apps/api"]\n'
        ),
    )
    assert _print_version() == "0.1.0"


def test_invalid_component_path_map_blocks_version_before_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build_monorepo(
        tmp_path,
        monkeypatch,
        extra_bsr=(
            "\n[tool.semantic_release.bsr]\n"
            "schema_version = 1\n"
            "[tool.semantic_release.bsr.component_path_map]\n"
            "schema_version = 2\n"
        ),
    )

    result = CliRunner(mix_stderr=True).invoke(main, ["--noop", "version", "--print"])

    assert result.exit_code != 0
    assert "schema_version" in result.output


def test_without_path_filter_web_only_commit_still_bumps_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `[tool.semantic_release.bsr]` table -- drop-in, identical to stock PSR: bumps."""
    _build_monorepo(tmp_path, monkeypatch)
    assert _print_version() != "0.1.0"


def test_path_filter_default_paths_from_run_directory_excludes_web_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `path_filter=true` with `paths` left EMPTY defaults to the run directory
    (the GH Action's `directory:`, i.e. `os.getcwd()` at CLI invocation time)
    made relative to the repo root. This is the Round 1 finding's regression
    test: the wiring must source the run directory from somewhere other than
    `runtime.repo_dir` (always the repo root), or this default silently
    becomes a no-op.
    """
    _build_monorepo(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\npath_filter = true\n",
    )
    assert _print_version() == "0.1.0"


def test_wiring_reaches_both_next_version_and_changelog_call_sites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Spy on both vendored call sites (`next_version` and
    `ReleaseHistory.from_git_history`) to prove `commit_path_filter` is wired
    into BOTH -- not just the one `--print` exercises -- and that it is the
    SAME resolved filter (non-None when enabled, None when off/default).

    The new commit touches `apps/api` itself (kept by the filter, not
    excluded) so a release actually proceeds far enough to reach the
    changelog call site too -- an excluded/no-bump run exits before that.
    """
    _build_monorepo(
        tmp_path,
        monkeypatch,
        extra_bsr=(
            "\n[tool.semantic_release.bsr]\n"
            "path_filter = true\n"
            'paths = ["apps/api"]\n'
        ),
        second_commit_relpath="apps/api/feature2.py",
    )

    captured: dict[str, object] = {}
    real_next_version = version_module.next_version
    real_from_git_history = ReleaseHistory.from_git_history

    def _spy_next_version(*args: object, **kwargs: object) -> object:
        captured["next_version"] = kwargs.get("commit_path_filter")
        return real_next_version(*args, **kwargs)  # type: ignore[arg-type]

    def _spy_from_git_history(**kwargs: object) -> ReleaseHistory:
        captured["from_git_history"] = kwargs.get("commit_path_filter")
        return real_from_git_history(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(version_module, "next_version", _spy_next_version)
    monkeypatch.setattr(ReleaseHistory, "from_git_history", _spy_from_git_history)

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0, result.output
    assert callable(captured["next_version"])
    assert captured["from_git_history"] is captured["next_version"]


def test_wiring_passes_none_to_both_call_sites_when_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (no bsr path_filter table) -- both call sites receive `None` -- drop-in."""
    _build_monorepo(tmp_path, monkeypatch)

    captured: dict[str, object] = {}
    real_next_version = version_module.next_version
    real_from_git_history = ReleaseHistory.from_git_history

    def _spy_next_version(*args: object, **kwargs: object) -> object:
        captured["next_version"] = kwargs.get("commit_path_filter")
        return real_next_version(*args, **kwargs)  # type: ignore[arg-type]

    def _spy_from_git_history(**kwargs: object) -> ReleaseHistory:
        captured["from_git_history"] = kwargs.get("commit_path_filter")
        return real_from_git_history(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(version_module, "next_version", _spy_next_version)
    monkeypatch.setattr(ReleaseHistory, "from_git_history", _spy_from_git_history)

    result = CliRunner(mix_stderr=True).invoke(
        main, ["--noop", "version", "--no-commit", "--no-tag", "--no-push"]
    )
    assert result.exit_code == 0, result.output
    assert captured["next_version"] is None
    assert captured["from_git_history"] is None
