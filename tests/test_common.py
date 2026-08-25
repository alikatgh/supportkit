"""Guards for the promoted helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from supportkit.common import parse_json_object, short_path


class TestShortPath:
    def test_inside_the_root_is_shortened(self, tmp_path: Path) -> None:
        assert short_path(tmp_path / "a" / "b.txt", tmp_path) == Path("a/b.txt")

    def test_outside_the_root_is_returned_unchanged_not_raised(self, tmp_path: Path) -> None:
        """The bug: a cosmetic call crashing after the real work finished."""
        other = Path("/private/tmp/elsewhere/out")
        assert short_path(other, tmp_path) == other


class TestParseJsonObject:
    def test_plain_object(self) -> None:
        assert parse_json_object('{"a": 1}') == {"a": 1}

    def test_fenced_and_prose_wrapped(self) -> None:
        assert parse_json_object('Sure!\n```json\n{"a": 1}\n```\nHope that helps.') == {"a": 1}

    def test_a_bare_array_raises_so_retry_loops_fire(self) -> None:
        with pytest.raises(ValueError):
            parse_json_object("[1, 2, 3]")

    def test_garbage_raises_rather_than_returning_empty(self) -> None:
        """A silent {} defeats the caller's retry loop and hides the error."""
        with pytest.raises(json.JSONDecodeError):
            parse_json_object("no json here at all")
