"""Deterministic seeded property tests for Version, VersionTranslator, and AngularCommitParser."""

from __future__ import annotations

import random
import string
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from semantic_release.commit_parser.angular import AngularCommitParser
from semantic_release.commit_parser.token import ParsedCommit, ParseError
from semantic_release.enums import LevelBump
from semantic_release.errors import InvalidVersion
from semantic_release.version.translator import VersionTranslator
from semantic_release.version.version import Version

if TYPE_CHECKING:  # pragma: no cover
    from git.objects.commit import Commit


def test_version_parse_ordering_properties() -> None:
    rng = random.Random(20261124)  # noqa: S311 - deterministic test PRNG
    samples: list[Version] = []
    for _ in range(120):
        major = rng.randint(0, 20)
        minor = rng.randint(0, 20)
        patch = rng.randint(0, 20)
        prerelease = rng.choice([None, "alpha", "beta", "rc"])
        if prerelease is None:
            raw = f"{major}.{minor}.{patch}"
        else:
            raw = f"{major}.{minor}.{patch}-{prerelease}.{rng.randint(1, 9)}"
        parsed = Version.parse(raw, prerelease_token="rc")
        assert str(parsed) == raw
        assert parsed == parsed  # noqa: PLR0124 - reflexive ordering invariant
        assert not (parsed < parsed)  # noqa: PLR0124 - irreflexive invariant
        samples.append(parsed)

    for left in samples[:40]:
        for right in samples[:40]:
            if left < right:
                assert not (right < left)
                assert right > left


def test_version_translator_roundtrip_properties() -> None:
    rng = random.Random(20261124)  # noqa: S311 - deterministic test PRNG
    for tag_format in ("v{version}", "{version}", "pkg-{version}"):
        translator = VersionTranslator(tag_format=tag_format)
        for _ in range(50):
            version_str = (
                f"{rng.randint(0, 15)}.{rng.randint(0, 30)}.{rng.randint(0, 50)}"
            )
            tag = translator.str_to_tag(version_str)
            decoded = translator.from_tag(tag)
            assert decoded is not None
            assert str(decoded) == version_str
        if tag_format == "{version}":
            with pytest.raises(InvalidVersion):
                translator.from_tag(f"unrelated-{tag_format}")
        else:
            assert translator.from_tag(f"unrelated-{tag_format}") is None


def test_angular_commit_parser_fuzz_and_monotonicity() -> None:
    parser = AngularCommitParser()
    rng = random.Random(20261124)  # noqa: S311 - deterministic test PRNG

    expected = [
        ("chore: sync deps", LevelBump.NO_RELEASE),
        ("fix(core): handle edge case", LevelBump.PATCH),
        ("feat(api): add endpoint", LevelBump.MINOR),
        ("feat!: drop legacy format", LevelBump.MAJOR),
    ]
    bumps: list[LevelBump] = []
    for message, expected_bump in expected:
        fake = cast(
            "Commit", SimpleNamespace(hexsha="a" * 40, message=message.encode("utf-8"))
        )
        results = parser.parse(fake)
        assert isinstance(results, list)
        assert len(results) == 1
        first = results[0]
        assert isinstance(first, ParsedCommit)
        assert first.bump == expected_bump
        bumps.append(first.bump)
    assert bumps[0] < bumps[1] < bumps[2] < bumps[3]

    alphabet = string.ascii_letters + string.digits + ":!()[] \n\t-_"
    for _ in range(200):
        noise = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        fake = cast(
            "Commit", SimpleNamespace(hexsha="b" * 40, message=noise.encode("utf-8"))
        )
        parsed = parser.parse(fake)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1
        assert all(isinstance(item, (ParsedCommit, ParseError)) for item in parsed)
