from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from semantic_release.bsr.messages import format_actionable, tag_format_sanity_note
from semantic_release.errors import MissingGitRemote, ParserLoadError


def _pydantic_validation_error() -> ValidationError:
    class _BranchMatch(BaseModel):
        match: str = Field(pattern=r"^only-a-b-c$")

    try:
        _BranchMatch.model_validate({"match": "not-matching"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


class TestFormatActionablePrereleaseBumpMismatch:
    def test_maps_1442_value_error(self) -> None:
        exc = ValueError(
            "Cannot increment a non-prerelease version with a prerelease level bump"
        )
        msg = format_actionable(exc)
        assert msg is not None
        assert "PRERELEASE BUMP MISMATCH" in msg
        assert "--as-prerelease" in msg or "--prerelease" in msg

    def test_unrelated_value_error_is_unmapped(self) -> None:
        assert format_actionable(ValueError("some other unrelated failure")) is None


class TestFormatActionableValidationError:
    def test_maps_931_validation_error_points_to_key(self) -> None:
        msg = format_actionable(_pydantic_validation_error())
        assert msg is not None
        assert "INVALID CONFIGURATION" in msg
        assert "match" in msg


class TestFormatActionableParserLoadError:
    def test_maps_parser_load_error(self) -> None:
        exc = ParserLoadError("Unrecognized commit parser value: 'bogus'.")
        msg = format_actionable(exc)
        assert msg is not None
        assert "PARSER LOAD FAILED" in msg
        assert "commit_parser" in msg


class TestFormatActionableMissingGitRemote:
    def test_maps_missing_remote_error(self) -> None:
        exc = MissingGitRemote("Unable to locate remote named 'origin'.")
        msg = format_actionable(exc)
        assert msg is not None
        assert "GIT REMOTE NOT FOUND" in msg
        assert "git remote add" in msg


class TestFormatActionableFallback:
    def test_unmapped_exception_type_returns_none(self) -> None:
        assert format_actionable(RuntimeError("totally unrelated")) is None


class TestTagFormatSanityNote:
    def test_note_when_tags_exist_but_none_matched(self) -> None:
        note = tag_format_sanity_note(
            total_tags=5, matched_tags=0, tag_format="v{version}"
        )
        assert note is not None
        assert "5" in note
        assert "v{version}" in note

    def test_no_note_when_no_tags(self) -> None:
        assert (
            tag_format_sanity_note(
                total_tags=0, matched_tags=0, tag_format="v{version}"
            )
            is None
        )

    def test_no_note_when_some_tags_matched(self) -> None:
        assert (
            tag_format_sanity_note(
                total_tags=5, matched_tags=3, tag_format="v{version}"
            )
            is None
        )
