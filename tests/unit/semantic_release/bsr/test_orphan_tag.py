from __future__ import annotations

from pathlib import Path

import pytest
from git import Actor, Repo

from semantic_release.bsr.errors import BsrGuardError
from semantic_release.bsr.orphan_tag import check_orphan_tag
from semantic_release.version.translator import VersionTranslator


def _commit(repo: Repo, msg: str) -> None:
    (Path(repo.working_tree_dir) / "f.txt").write_text(msg, encoding="utf-8")
    repo.index.add(["f.txt"])
    repo.index.commit(msg, author=Actor("t", "t@t"), committer=Actor("t", "t@t"))


@pytest.fixture
def translator() -> VersionTranslator:
    return VersionTranslator(tag_format="v{version}")


def test_no_tags_is_ok(tmp_path: Path, translator: VersionTranslator):
    repo = Repo.init(tmp_path)
    _commit(repo, "feat: a")
    check_orphan_tag(repo, translator)  # must not raise


def test_reachable_highest_tag_ok(tmp_path: Path, translator: VersionTranslator):
    repo = Repo.init(tmp_path)
    _commit(repo, "feat: a")
    repo.create_tag("v0.1.0")
    _commit(repo, "feat: b")
    repo.create_tag("v0.2.0")  # on HEAD -> reachable
    check_orphan_tag(repo, translator)  # must not raise


def test_orphan_highest_tag_raises(tmp_path: Path, translator: VersionTranslator):
    repo = Repo.init(tmp_path)
    _commit(repo, "feat: a")
    c1 = repo.head.commit
    repo.create_tag("v0.1.0", ref=c1)
    _commit(repo, "feat: b")
    repo.create_tag("v0.2.0")  # highest, on c2
    # Rewind main to c1 so v0.2.0 (on c2) is no longer an ancestor of HEAD.
    repo.head.reference.set_commit(c1)
    repo.head.reset(index=True, working_tree=True)
    with pytest.raises(BsrGuardError) as exc:
        check_orphan_tag(repo, translator)
    assert "v0.2.0" in exc.value.message
