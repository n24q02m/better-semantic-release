"""
Unit tests for `bsr/summary.py` (C3): `resolve_components`, `build_summary` /
`build_component_plan`, `render_summary_table` -- exercised directly against a
real git fixture + `AngularCommitParser`/`VersionTranslator`, not through the
CLI (see test_summary_cli.py for the `# BSR-PATCH` wiring in version.py).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from semantic_release.bsr.config import BsrComponent, BsrConfig
from semantic_release.bsr.summary import (
    ComponentPlan,
    build_component_plan,
    build_summary,
    render_summary_table,
    resolve_components,
)
from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.version.translator import VersionTranslator
from semantic_release.version.version import Version

if TYPE_CHECKING:
    from git.objects.commit import Commit

_AUTHOR = Actor("t", "t@t")


def _commit(repo: Repo, relpath: str, content: str, message: str) -> Commit:
    file_path = Path(str(repo.working_tree_dir)) / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    repo.index.add([relpath])
    return repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


# --- resolve_components -----------------------------------------------------


def test_resolve_components_uses_configured_components_when_present() -> None:
    configured = (
        BsrComponent(name="api", paths=("apps/api",)),
        BsrComponent(name="web", paths=("apps/web",)),
    )
    cfg = BsrConfig(components=configured)
    assert resolve_components(cfg, default_name="ignored") == configured


def test_resolve_components_falls_back_to_single_component_from_paths() -> None:
    cfg = BsrConfig(components=(), paths=("apps/api",))
    assert resolve_components(cfg, default_name="demo") == (
        BsrComponent(name="demo", paths=("apps/api",)),
    )


def test_resolve_components_falls_back_with_empty_paths_when_paths_absent() -> None:
    cfg = BsrConfig()
    assert resolve_components(cfg, default_name="demo") == (
        BsrComponent(name="demo", paths=()),
    )


# --- build_component_plan / build_summary -----------------------------------


def _build_two_component_repo(tmp_path: Path) -> Repo:
    """
    `apps/api` released at v0.1.0; ONE new `feat` commit touches only
    `apps/web` afterwards -- `api` must show no release, `web` must show a
    MINOR release.
    """
    repo = Repo.init(tmp_path)
    _commit(repo, "apps/api/x.py", "x", "feat: initial api")
    repo.create_tag("v0.1.0")
    _commit(repo, "apps/web/y.py", "y", "feat: add web page")
    return repo


def _build_summary(
    repo: Repo, components: tuple[BsrComponent, ...]
) -> tuple[ComponentPlan, ...]:
    return build_summary(
        components,
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        prerelease=False,
        major_on_zero=True,
        allow_zero_version=True,
    )


def test_build_component_plan_no_matching_commits_reports_no_release(
    tmp_path: Path,
) -> None:
    repo = _build_two_component_repo(tmp_path)
    plan = build_component_plan(
        BsrComponent(name="api", paths=("apps/api",)),
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        prerelease=False,
        major_on_zero=True,
        allow_zero_version=True,
    )
    assert plan.name == "api"
    assert plan.would_release is False
    assert plan.level == "NO_RELEASE"
    assert plan.commit_count == 0
    assert plan.sample_paths == ()
    assert plan.resulting_version == Version.parse("0.1.0")


def test_build_component_plan_matching_feat_commit_reports_minor_release(
    tmp_path: Path,
) -> None:
    repo = _build_two_component_repo(tmp_path)
    plan = build_component_plan(
        BsrComponent(name="web", paths=("apps/web",)),
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        prerelease=False,
        major_on_zero=True,
        allow_zero_version=True,
    )
    assert plan.name == "web"
    assert plan.would_release is True
    assert plan.level == "MINOR"
    assert plan.commit_count == 1
    assert plan.sample_paths == ("apps/web/y.py",)
    assert plan.resulting_version == Version.parse("0.2.0")


def test_build_summary_returns_one_plan_per_component(tmp_path: Path) -> None:
    repo = _build_two_component_repo(tmp_path)
    components = (
        BsrComponent(name="api", paths=("apps/api",)),
        BsrComponent(name="web", paths=("apps/web",)),
    )
    plans = _build_summary(repo, components)
    assert [p.name for p in plans] == ["api", "web"]
    assert [p.would_release for p in plans] == [False, True]


def test_build_component_plan_empty_paths_is_whole_repo_passthrough(
    tmp_path: Path,
) -> None:
    """A component with no configured `paths` matches every commit (M2's no-op passthrough)."""
    repo = _build_two_component_repo(tmp_path)
    plan = build_component_plan(
        BsrComponent(name="(repo)", paths=()),
        repo=repo,
        translator=VersionTranslator(),
        commit_parser=AngularCommitParser(),  # type: ignore[arg-type]
        prerelease=False,
        major_on_zero=True,
        allow_zero_version=True,
    )
    assert plan.would_release is True
    assert plan.commit_count == 1
    assert plan.resulting_version == Version.parse("0.2.0")


# --- render_summary_table ----------------------------------------------------


def test_render_summary_table_contains_headers_and_rows() -> None:
    plans = (
        ComponentPlan(
            name="api",
            would_release=False,
            level="NO_RELEASE",
            commit_count=0,
            sample_paths=(),
            resulting_version=Version.parse("0.1.0"),
        ),
        ComponentPlan(
            name="web",
            would_release=True,
            level="MINOR",
            commit_count=1,
            sample_paths=("apps/web/y.py",),
            resulting_version=Version.parse("0.2.0"),
        ),
    )
    table = render_summary_table(plans)
    for header in (
        "component",
        "would-release",
        "level",
        "commits",
        "sample paths",
        "version",
    ):
        assert header in table
    assert "api" in table
    assert "no" in table
    assert "NO_RELEASE" in table
    assert "web" in table
    assert "yes" in table
    assert "MINOR" in table
    assert "apps/web/y.py" in table
    assert "0.2.0" in table


def test_render_summary_table_empty_plans_still_renders_headers() -> None:
    table = render_summary_table(())
    assert "component" in table
    assert "would-release" in table
