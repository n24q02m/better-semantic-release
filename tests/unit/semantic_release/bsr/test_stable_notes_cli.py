"""
CLI integration for `bsr.stable_notes_aggregate` (C4): exercises the real
`# BSR-PATCH` seam in `cli/commands/version.py` via REAL (non-`--noop`)
sequential `version` invocations -- v0.1.0 (stable) -> v0.2.0-beta.1 ->
v0.2.0-beta.2 -> v0.2.0 (stable finalize, zero brand-new commits) -- the
exact "consumed by prerelease" shape (issue #555). See test_stable_notes.py
for the synthetic scope/dedup unit matrix and test_stable_notes_changelog.py
for direct template-rendering coverage; see test_stable_notes_parity.py for
the flag-off byte-identical proof.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("t", "t@t")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pyproject_toml(extra_bsr: str) -> str:
    extra_bsr = extra_bsr.replace(
        "\n[tool.semantic_release.bsr]\n",
        "\n[tool.semantic_release.bsr]\nschema_version = 1\n",
        1,
    )
    return (
        '[project]\nname = "demo-pkg"\nversion = "0.1.0"\n\n'
        "[tool.semantic_release]\n"
        'version_toml = ["pyproject.toml:project.version"]\n'
        'tag_format = "v{version}"\n'
        # NOTE: PSR forces a MAJOR bump out of 0.x.x version whenever
        # `allow_zero_version` is False (the PSR default) -- regardless of
        # whether there were any releasable commits at all. Setting it True
        # here keeps the "no commits matched" path from becoming a major bump.
        "allow_zero_version = true\n" + extra_bsr
    )


def _build_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_bsr: str = ""
) -> Path:
    proj = tmp_path / "proj"
    _write(proj / "pyproject.toml", _pyproject_toml(extra_bsr))
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    monkeypatch.chdir(proj)
    return proj


def _commit(proj: Path, relpath: str, content: str, message: str) -> None:
    _write(proj / relpath, content)
    repo = Repo(str(proj))
    repo.index.add([relpath])
    repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _run_version(*extra_args: str, env: dict[str, str] | None = None) -> object:
    """A REAL (non-`--noop`) `version` run: commits, tags, writes the changelog."""
    return CliRunner(mix_stderr=True).invoke(
        main, ["version", "--no-push", *extra_args], env=env
    )


def _tag_names(proj: Path) -> set[str]:
    return {t.name for t in Repo(str(proj)).tags}


def _section(rendered: str, version_tag: str) -> str:
    marker = f"## {version_tag} ("
    start = rendered.index(marker)
    rest = rendered[start:]
    end = rest.find("\n## ", 1)
    return rest if end == -1 else rest[:end]


def _release_beta_then_stable(proj: Path) -> None:
    """
    v0.1.0 (stable) -> v0.2.0-beta.1 ("feat: add thing A") -> v0.2.0-beta.2
    ("feat: add thing B") -> HEAD stays at beta.2's commit, so the pending
    stable finalize has ZERO brand-new commits of its own.
    """
    result = _run_version()
    assert result.exit_code == 0, result.output
    assert "v0.1.0" in _tag_names(proj)

    _commit(proj, "a.txt", "a", "feat: add thing A")
    result = _run_version("--as-prerelease", "--prerelease-token=beta")
    assert result.exit_code == 0, result.output
    assert "v0.2.0-beta.1" in _tag_names(proj)

    _commit(proj, "b.txt", "b", "feat: add thing B")
    result = _run_version("--as-prerelease", "--prerelease-token=beta")
    assert result.exit_code == 0, result.output
    assert "v0.2.0-beta.2" in _tag_names(proj)


def test_aggregate_on_merges_beta_commits_into_stable_changelog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nstable_notes_aggregate = true\n",
    )
    _release_beta_then_stable(proj)

    result = _run_version()
    assert result.exit_code == 0, result.output
    assert "v0.2.0" in _tag_names(proj)

    changelog = (proj / "CHANGELOG.md").read_text(encoding="utf-8").lower()
    section = _section(changelog, "v0.2.0")
    assert "thing a" in section
    assert "thing b" in section


def test_aggregate_off_by_default_leaves_stable_section_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline: no `[tool.semantic_release.bsr]` table at all -- issue #555 reproduced."""
    proj = _build_project(tmp_path, monkeypatch)
    _release_beta_then_stable(proj)

    result = _run_version()
    assert result.exit_code == 0, result.output
    assert "v0.2.0" in _tag_names(proj)

    changelog = (proj / "CHANGELOG.md").read_text(encoding="utf-8").lower()
    section = _section(changelog, "v0.2.0")
    assert "thing a" not in section
    assert "thing b" not in section


def test_aggregate_on_merges_beta_commits_into_github_release_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The GitHub release-notes body (`generate_release_notes`, surfaced via
    `$GITHUB_OUTPUT`'s `release_notes` key) is the OTHER consumer of the
    substituted `Release` -- must also aggregate, not just the changelog file.
    """
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nstable_notes_aggregate = true\n",
    )
    _release_beta_then_stable(proj)

    output_file = tmp_path / "gha.out"
    result = _run_version(env={"GITHUB_OUTPUT": str(output_file)})
    assert result.exit_code == 0, result.output

    gha_output = output_file.read_text(encoding="utf-8").lower()
    assert "thing a" in gha_output
    assert "thing b" in gha_output


def test_aggregate_off_github_release_notes_stay_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _build_project(tmp_path, monkeypatch)
    _release_beta_then_stable(proj)

    output_file = tmp_path / "gha.out"
    result = _run_version(env={"GITHUB_OUTPUT": str(output_file)})
    assert result.exit_code == 0, result.output

    gha_output = output_file.read_text(encoding="utf-8").lower()
    assert "thing a" not in gha_output
    assert "thing b" not in gha_output


def test_aggregate_with_no_changelog_flag_still_aggregates_release_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    `--no-changelog` skips `write_changelog_files` entirely, but
    `generate_release_notes` still runs unconditionally -- the aggregation
    seam sits BEFORE both consumers (not duplicated at each call site), so
    it must still take effect here.
    """
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nstable_notes_aggregate = true\n",
    )
    _release_beta_then_stable(proj)

    output_file = tmp_path / "gha.out"
    result = _run_version("--no-changelog", env={"GITHUB_OUTPUT": str(output_file)})
    assert result.exit_code == 0, result.output

    gha_output = output_file.read_text(encoding="utf-8").lower()
    assert "thing a" in gha_output
    assert "thing b" in gha_output


def test_aggregate_since_stable_scope_via_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`stable_notes_scope = "since_stable"` is readable end-to-end through the CLI."""
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_bsr=(
            "\n[tool.semantic_release.bsr]\n"
            "stable_notes_aggregate = true\n"
            'stable_notes_scope = "since_stable"\n'
        ),
    )
    _release_beta_then_stable(proj)

    result = _run_version()
    assert result.exit_code == 0, result.output

    changelog = (proj / "CHANGELOG.md").read_text(encoding="utf-8").lower()
    section = _section(changelog, "v0.2.0")
    assert "thing a" in section
    assert "thing b" in section


def test_aggregate_on_stdout_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The machine contract: stdout on the finalize run is ONLY the version, even when on."""
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nstable_notes_aggregate = true\n",
    )
    _release_beta_then_stable(proj)

    result = CliRunner(mix_stderr=False).invoke(main, ["version", "--no-push"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.2.0"


def test_aggregate_custom_release_notes_template_still_sees_aggregated_elements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A user-supplied `.release_notes.md.j2` override reads from the SAME
    substituted `Release` -- the seam sits above template selection, so a
    custom template also sees the aggregated elements, not just PSR's
    default template.
    """
    proj = _build_project(
        tmp_path,
        monkeypatch,
        extra_bsr="\n[tool.semantic_release.bsr]\nstable_notes_aggregate = true\n",
    )
    _write(
        proj / "templates" / ".release_notes.md.j2",
        "CUSTOM NOTES for {{ release.version.as_semver_tag() }}\n"
        "{% for type_, commits in release['elements'] | dictsort %}"
        "{% for c in commits %}- {{ c.descriptions[0] }}\n{% endfor %}"
        "{% endfor %}",
    )
    _release_beta_then_stable(proj)

    output_file = tmp_path / "gha.out"
    result = _run_version(env={"GITHUB_OUTPUT": str(output_file)})
    assert result.exit_code == 0, result.output

    gha_output = output_file.read_text(encoding="utf-8")
    assert "CUSTOM NOTES for v0.2.0" in gha_output
    assert "add thing A" in gha_output
    assert "add thing B" in gha_output
