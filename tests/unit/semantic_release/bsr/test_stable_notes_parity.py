"""
Drop-in parity proof for `bsr.stable_notes_aggregate` (C4) -- the most
important test in this feature: it touches the changelog/release-notes
CONTENT (the user's committed artifact), so `stable_notes_aggregate = false`
(explicit or, equivalently, no `[tool.semantic_release.bsr]` table at all)
MUST be byte-identical to stock PSR -- same stdout, same stderr (module
timestamps normalized), same exit code, same `CHANGELOG.md` file content,
same `$GITHUB_OUTPUT` `release_notes` value -- on the EXACT beta-then-stable
fixture where the feature, when ON, visibly changes all of those.

Two SEPARATE copies of the SAME already-built repo (post beta.1/beta.2, via
real non-noop runs) are diffed for the real (non-noop) stable finalize, so
the prior commits' hashes -- embedded in the rendered changelog -- are
identical across both copies; only the config differs.
"""

from __future__ import annotations

import re
import shutil
from typing import TYPE_CHECKING

from click.testing import CliRunner
from git import Actor, Repo

from semantic_release.bsr import guards
from semantic_release.bsr.registry import ProbeResult
from semantic_release.cli.commands.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_AUTHOR = Actor("t", "t@t")
_TIMESTAMP_RE = re.compile(r"\[\d{2}:\d{2}:\d{2}\]")


def _normalized(output: str) -> str:
    """Strip RichHandler's `[HH:MM:SS]` log prefix -- see test_explain_parity.py."""
    return _TIMESTAMP_RE.sub("[TS]", output)


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
        "allow_zero_version = true\n" + extra_bsr
    )


def _build_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    proj = tmp_path / "proj"
    _write(proj / "pyproject.toml", _pyproject_toml(""))
    repo = Repo.init(proj)
    repo.index.add(["pyproject.toml"])
    repo.index.commit("feat: initial", author=_AUTHOR, committer=_AUTHOR)
    repo.create_remote("origin", "https://github.com/example-owner/example-repo.git")
    monkeypatch.chdir(proj)
    monkeypatch.setattr(
        guards, "probe_registry", lambda *_args, **_kwargs: ProbeResult.FREE
    )
    return proj


def _commit(proj: Path, relpath: str, content: str, message: str) -> None:
    _write(proj / relpath, content)
    repo = Repo(str(proj))
    repo.index.add([relpath])
    repo.index.commit(message, author=_AUTHOR, committer=_AUTHOR)


def _run_version(*extra_args: str, env: dict[str, str] | None = None) -> object:
    return CliRunner(mix_stderr=True).invoke(
        main, ["version", "--no-push", *extra_args], env=env
    )


def _build_beta_then_stable_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """
    v0.1.0 (stable) -> v0.2.0-beta.1 -> v0.2.0-beta.2, via REAL (non-noop)
    runs, all with NO `[tool.semantic_release.bsr]` table -- the shared
    ancestor state both parity copies branch from.
    """
    proj = _build_project(tmp_path, monkeypatch)
    result = _run_version()
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]

    _commit(proj, "a.txt", "a", "feat: add thing A")
    result = _run_version("--as-prerelease", "--prerelease-token=beta")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]

    _commit(proj, "b.txt", "b", "feat: add thing B")
    result = _run_version("--as-prerelease", "--prerelease-token=beta")
    assert result.exit_code == 0, result.output  # type: ignore[attr-defined]

    return proj


def _append_bsr_table(proj: Path, extra_bsr: str) -> None:
    extra_bsr = extra_bsr.replace(
        "\n[tool.semantic_release.bsr]\n",
        "\n[tool.semantic_release.bsr]\nschema_version = 1\n",
        1,
    )
    pyproject = proj / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + extra_bsr, encoding="utf-8"
    )


def _section(rendered: str, version_tag: str) -> str:
    marker = f"## {version_tag} ("
    start = rendered.index(marker)
    rest = rendered[start:]
    end = rest.find("\n## ", 1)
    return rest if end == -1 else rest[:end]


def _release_notes_block(gha_output: str) -> str:
    """
    Extract just the `release_notes<<EOF ... EOF` block from a
    `$GITHUB_OUTPUT` file's content -- other keys (`commit_sha` in
    particular) legitimately differ between two SEPARATE `version`
    invocations regardless of this feature, since each makes its own fresh
    release-bump commit.
    """
    match = re.search(
        r"release_notes<<([a-zA-Z0-9_]+)\r?\n(.*?)\1\r?\n", gha_output, re.DOTALL
    )
    assert match is not None, gha_output
    return match.group(2)


def test_explicit_false_matches_no_bsr_table_stdout_stderr_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = _build_beta_then_stable_fixture(tmp_path, monkeypatch)

    proj_false = tmp_path / "proj_false"
    proj_no_table = tmp_path / "proj_no_table"
    shutil.copytree(shared, proj_false)
    shutil.copytree(shared, proj_no_table)
    _append_bsr_table(
        proj_false, "\n[tool.semantic_release.bsr]\nstable_notes_aggregate = false\n"
    )

    monkeypatch.chdir(proj_false)
    explicit_false = _run_version()

    monkeypatch.chdir(proj_no_table)
    no_bsr_table = _run_version()

    assert explicit_false.exit_code == no_bsr_table.exit_code == 0  # type: ignore[attr-defined]
    assert _normalized(explicit_false.output) == _normalized(no_bsr_table.output)  # type: ignore[attr-defined]


def test_explicit_false_matches_no_bsr_table_changelog_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core C4 parity claim: the WRITTEN `CHANGELOG.md` file, byte-for-byte."""
    shared = _build_beta_then_stable_fixture(tmp_path, monkeypatch)

    proj_false = tmp_path / "proj_false"
    proj_no_table = tmp_path / "proj_no_table"
    shutil.copytree(shared, proj_false)
    shutil.copytree(shared, proj_no_table)
    _append_bsr_table(
        proj_false, "\n[tool.semantic_release.bsr]\nstable_notes_aggregate = false\n"
    )

    monkeypatch.chdir(proj_false)
    explicit_false = _run_version()
    assert explicit_false.exit_code == 0  # type: ignore[attr-defined]

    monkeypatch.chdir(proj_no_table)
    no_bsr_table = _run_version()
    assert no_bsr_table.exit_code == 0  # type: ignore[attr-defined]

    changelog_false = (proj_false / "CHANGELOG.md").read_text(encoding="utf-8")
    changelog_no_table = (proj_no_table / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog_false == changelog_no_table
    # sanity: this parity fixture DOES exhibit the bug the feature fixes, so
    # the comparison above is not vacuously true (both sides would trivially
    # match if neither ever aggregated anything different from empty). "thing
    # a"/"thing b" DO appear elsewhere in the full changelog (under their own
    # beta.1/beta.2 sections) -- the bug is specifically an EMPTY v0.2.0 section.
    stable_section = _section(changelog_false, "v0.2.0").lower()
    assert "thing a" not in stable_section
    assert "thing b" not in stable_section


def test_explicit_false_matches_no_bsr_table_release_notes_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same proof for the OTHER consumer: the `$GITHUB_OUTPUT` `release_notes` body."""
    shared = _build_beta_then_stable_fixture(tmp_path, monkeypatch)

    proj_false = tmp_path / "proj_false"
    proj_no_table = tmp_path / "proj_no_table"
    shutil.copytree(shared, proj_false)
    shutil.copytree(shared, proj_no_table)
    _append_bsr_table(
        proj_false, "\n[tool.semantic_release.bsr]\nstable_notes_aggregate = false\n"
    )

    output_false = tmp_path / "gha_false.out"
    monkeypatch.chdir(proj_false)
    explicit_false = _run_version(env={"GITHUB_OUTPUT": str(output_false)})
    assert explicit_false.exit_code == 0  # type: ignore[attr-defined]

    output_no_table = tmp_path / "gha_no_table.out"
    monkeypatch.chdir(proj_no_table)
    no_bsr_table = _run_version(env={"GITHUB_OUTPUT": str(output_no_table)})
    assert no_bsr_table.exit_code == 0  # type: ignore[attr-defined]

    notes_false = _release_notes_block(output_false.read_text(encoding="utf-8"))
    notes_no_table = _release_notes_block(output_no_table.read_text(encoding="utf-8"))
    assert notes_false == notes_no_table


def test_default_off_stdout_is_only_the_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Separated-stream proof (not merged like the tests above): with `bsr`
    entirely absent, real-run stdout is ONLY the version -- the hard "STDOUT
    is a machine contract" constraint, unaffected by this feature existing.
    """
    shared = _build_beta_then_stable_fixture(tmp_path, monkeypatch)
    monkeypatch.chdir(shared)
    result = CliRunner(mix_stderr=False).invoke(main, ["version", "--no-push"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.2.0"


def test_on_actually_differs_from_off_not_a_vacuous_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Guards the parity tests above against being vacuously true: with the
    SAME shared history, `stable_notes_aggregate = true` DOES produce a
    different `CHANGELOG.md` than the default-off copies.
    """
    shared = _build_beta_then_stable_fixture(tmp_path, monkeypatch)

    proj_on = tmp_path / "proj_on"
    proj_off = tmp_path / "proj_off"
    shutil.copytree(shared, proj_on)
    shutil.copytree(shared, proj_off)
    _append_bsr_table(
        proj_on, "\n[tool.semantic_release.bsr]\nstable_notes_aggregate = true\n"
    )

    monkeypatch.chdir(proj_on)
    result_on = _run_version()
    assert result_on.exit_code == 0  # type: ignore[attr-defined]

    monkeypatch.chdir(proj_off)
    result_off = _run_version()
    assert result_off.exit_code == 0  # type: ignore[attr-defined]

    changelog_on = (proj_on / "CHANGELOG.md").read_text(encoding="utf-8")
    changelog_off = (proj_off / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog_on != changelog_off
