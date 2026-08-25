"""Guards for the parser tournament. Cases come from a real corpus."""

from __future__ import annotations

from datetime import datetime

from supportkit.dateparse import (
    Reading,
    evaluate,
    get_parser,
    parse_ru_half,
    parse_zh_half,
    tournament,
)


class TestBilingual:
    def test_genitive_and_nominative_months_both_parse(self) -> None:
        assert parse_ru_half("9 марта(3月9日)") == Reading(month=3, day=9)
        assert parse_ru_half("3 Август (8月3日)") == Reading(month=8, day=3)

    def test_march_is_not_read_as_may(self) -> None:
        """'ма' prefixes both 'март' and 'май'; the table order makes март win.
        Reordering RU_MONTHS is the known sabotage that turns this red."""
        assert parse_ru_half("9 марта(3月9日)") == Reading(month=3, day=9)
        assert parse_ru_half("5 мая(5月5日)") == Reading(month=5, day=5)

    def test_halves_that_disagree_are_two_readings_not_a_coin_flip(self) -> None:
        readings = get_parser("ru_zh_bilingual").parse("6 апрель(4月4日)")
        assert {(r.month, r.day) for r in readings} == {(4, 6), (4, 4)}

    def test_agreeing_halves_deduplicate(self) -> None:
        assert len(get_parser("ru_zh_bilingual").parse("9 марта(3月9日)")) == 1

    def test_a_year_in_the_text_is_attached(self) -> None:
        readings = get_parser("ru_zh_bilingual").parse("9 марта 2026(3月9日)")
        assert all(r.year == 2026 for r in readings)

    def test_zh_half_parses_independently(self) -> None:
        assert parse_zh_half("(3月9日)") == Reading(month=3, day=9)
        assert parse_zh_half("no date") is None


class TestIsoLike:
    def test_real_datetime_cells_from_openpyxl(self) -> None:
        assert get_parser("iso_like").parse(datetime(2026, 8, 26, 12, 0)) == [
            Reading(month=8, day=26, year=2026)]

    def test_iso_string(self) -> None:
        assert get_parser("iso_like").parse("2026-08-26") == [Reading(month=8, day=26, year=2026)]

    def test_european_dot_form_is_unambiguous(self) -> None:
        assert get_parser("iso_like").parse("26.08.2026") == [Reading(month=8, day=26, year=2026)]

    def test_ambiguous_slash_is_a_self_conflict_not_a_silent_default(self) -> None:
        """03/04/2026 is defensible both ways; a wizard must see the ambiguity."""
        readings = get_parser("iso_like").parse("03/04/2026")
        assert {(r.month, r.day) for r in readings} == {(4, 3), (3, 4)}

    def test_unambiguous_slash_because_day_exceeds_twelve(self) -> None:
        assert get_parser("iso_like").parse("26/08/2026") == [Reading(month=8, day=26, year=2026)]


class TestTournament:
    def test_the_right_parser_wins_on_bilingual_data(self) -> None:
        values = ["9 марта(3月9日)", "27 Июля (7月27日)", "3 Август (8月3日)", "", None]
        scores = tournament(values)
        assert scores[0].name == "ru_zh_bilingual"
        assert scores[0].rate == 1.0

    def test_the_right_parser_wins_on_iso_data(self) -> None:
        scores = tournament(["2026-01-02", "2026-01-03", "2026-01-04", "junk", "2026-01-05", "2026-01-06"])
        assert scores[0].name == "iso_like"

    def test_conflicts_and_missing_years_are_counted(self) -> None:
        score = evaluate(get_parser("ru_zh_bilingual"),
                         ["6 апрель(4月4日)", "9 марта(3月9日)"])
        assert score.conflicts == 1
        assert score.with_year == 0
        assert "no value carries a year" in score.evidence()

    def test_blank_values_do_not_inflate_the_denominator(self) -> None:
        score = evaluate(get_parser("iso_like"), ["2026-01-02", "", None, "   "])
        assert score.total == 1 and score.rate == 1.0
