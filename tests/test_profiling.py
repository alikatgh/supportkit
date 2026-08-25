"""Guards for the generic profiler."""

from __future__ import annotations

from supportkit.pii import Scrubber
from supportkit.profiling import (
    classify_column,
    find_header_row,
    month_coverage,
    profile_sheet,
)


class TestHeaderAndMarkers:
    def test_banner_sheets_find_the_real_header(self) -> None:
        rows = [("Summary report", None, None), (None, None, None),
                ("Date", "Category", "Status"), ("2026-01-02", "billing", "open")]
        assert find_header_row(rows) == 2

    def test_instruction_rows_are_excluded_and_reported(self) -> None:
        """A '示例' banner row carrying a stray status once inflated a count."""
        rows = [("Date", "Category", "Status"),
                ("以下两行为示例模板哈：", None, "resolved"),
                ("2026-01-02", "billing", "open")]
        profile, body, _ = profile_sheet("s", rows)
        assert profile.data_rows == 1
        assert profile.marker_rows == (2,)

    def test_a_full_row_mentioning_a_keyword_is_still_data(self) -> None:
        """'example.com in the problem text' is a record, not an instruction."""
        rows = [("Date", "Category", "Problem", "Status", "Operator"),
                ("2026-01-02", "billing", "see example.com please", "open", "anna")]
        profile, _, _ = profile_sheet("s", rows)
        assert profile.data_rows == 1
        assert profile.marker_rows == ()


class TestClassification:
    def test_kinds(self) -> None:
        assert classify_column("n", ["1", "2", "3"]).kind == "numeric"
        assert classify_column("s", ["open"] * 40 + ["closed"] * 10).kind == "categorical"
        assert classify_column("t", [f"{'long text ' * 12}{i}" for i in range(40)]).kind == "free text"

    def test_whitespace_only_cells_are_blank(self) -> None:
        col = classify_column("x", ["   ", None, "", "value"])
        assert col.non_null == 1 and col.null_rate == 0.75

    def test_top_values_are_pseudonymised(self) -> None:
        """A profile is a derived file; identifiers must not reach it."""
        col = classify_column("uid", ["8823174", "8823174", "9911223"], Scrubber())
        shown = " ".join(v for v, _ in col.top)
        assert "8823174" not in shown and "<ID_" in shown

    def test_distinct_identifiers_get_distinct_placeholders(self) -> None:
        col = classify_column("uid", ["8823174", "9911223"], Scrubber())
        values = [v for v, _ in col.top]
        assert len(set(values)) == 2


class TestMonthCoverage:
    def test_partial_months_are_flagged(self) -> None:
        pairs = [(8, d) for d in (1, 10, 20)] + [(7, 1), (7, 31)]
        by_month = {c.month: c for c in month_coverage(pairs, reference_year=2026)}
        assert by_month[8].partial and by_month[8].days_covered == 20
        assert not by_month[7].partial

    def test_per_day_can_reverse_the_raw_ranking(self) -> None:
        pairs = [(7, (d % 31) + 1) for d in range(31)] + [(8, (d % 20) + 1) for d in range(25)]
        by_month = {c.month: c for c in month_coverage(pairs, reference_year=2026)}
        assert by_month[8].contacts < by_month[7].contacts
        assert by_month[8].per_day > by_month[7].per_day
