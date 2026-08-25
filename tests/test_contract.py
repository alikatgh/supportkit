"""Guards for the mapping proposer. The load-bearing rule: measured beats named."""

from __future__ import annotations

from pathlib import Path

from supportkit.contract import Mapping, propose_mapping
from supportkit.profiling import column_values, profile_sheet


def _propose(rows):
    profile, body, _ = profile_sheet("s", rows)
    values = {c.name: column_values(body, c.index) for c in profile.columns}
    return propose_mapping(profile, values), profile


class TestMeasuredBeatsNamed:
    def test_a_junk_column_named_date_loses_to_a_parsing_column_named_misc(self) -> None:
        rows = [("date", "misc", "note")]
        for i in range(12):
            rows.append((f"ref-{i}", f"2026-01-{i + 1:02d}", f"free note text {i}"))
        proposal, _ = _propose(rows)
        assert proposal.mapping.role("date") == "misc"
        assert "measured" in proposal.evidence["date"]

    def test_a_hinted_name_cannot_beat_a_higher_measured_rate(self) -> None:
        """The rule the proposer exists for, tested where it can actually bite.

        The first version of this test used a junk 'date' column, which the
        rate floor filtered before name-preference was ever consulted — so
        sabotaging the priority left it green. Here BOTH columns parse: the
        hinted one at ~67%, the unhinted one at 100%. Only the ordering of
        (rate, hint) decides, and inverting it is the sabotage that goes red.
        """
        rows = [("date", "misc")]
        for i in range(12):
            hinted = f"2026-01-{i + 1:02d}" if i % 3 else "junk"     # ~67% parse
            rows.append((hinted, f"2026-02-{i + 1:02d}"))            # 100% parse
        proposal, _ = _propose(rows)
        assert proposal.mapping.role("date") == "misc", (
            "the header hint outvoted a higher measured parse rate"
        )

    def test_no_column_parses_means_no_date_role_not_a_guess(self) -> None:
        rows = [("date", "note")] + [(f"ref-{i}", f"text {i}") for i in range(12)]
        proposal, _ = _propose(rows)
        assert proposal.mapping.role("date") is None


class TestRoleProposals:
    def _sheet(self):
        rows = [("Дата обращения(受理时间)", "Категория(咨询分类)", "Проблема(咨询问题)",
                 "Статус(处理进度)", "UID", "Оператор(接待人)")]
        problems = [
            "Отсутствует аккомпанемент и вообще ничего не работает уже неделю, помогите пожалуйста разобраться " * 2,
            "Как записывать с аккомпанементом, не нахожу кнопку и настройки записи в новом интерфейсе приложения " * 2,
        ]
        for i in range(30):
            rows.append((f"{(i % 27) + 1} марта(3月{(i % 27) + 1}日)",
                         ["функции", "оплата", "аккаунт"][i % 3],
                         problems[i % 2] + str(i),
                         ["Решено", "Не решена"][i % 2],
                         str(650000 + i),
                         ["Maksim", "Anna"][i % 2]))
        return rows

    def test_the_bilingual_corpus_shape_maps_fully(self) -> None:
        proposal, _ = self._sheet(), None
        proposal, _ = _propose(self._sheet())
        mapped = proposal.mapping
        assert mapped.role("date") == "Дата обращения(受理时间)"
        assert mapped.date_parser == "ru_zh_bilingual"
        assert mapped.role("free_text") == "Проблема(咨询问题)"
        assert mapped.role("status") == "Статус(处理进度)"
        assert mapped.role("user_id") == "UID"
        assert mapped.role("operator") == "Оператор(接待人)"
        assert mapped.role("category") == "Категория(咨询分类)"

    def test_every_choice_carries_evidence(self) -> None:
        proposal, _ = _propose(self._sheet())
        for role in proposal.mapping.columns:
            assert proposal.evidence.get(role), f"no evidence recorded for {role}"


class TestMappingRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        mapping = Mapping(columns={"date": "d", "category": "c"}, date_parser="iso_like",
                          rulings={"blank_quantity": "not_counted"})
        mapping.save(tmp_path / "m.json")
        loaded = Mapping.load(tmp_path / "m.json")
        assert loaded.to_dict() == mapping.to_dict()

    def test_validate_reports_a_column_that_is_not_in_the_sheet(self) -> None:
        _, profile = _propose([("a", "b"), ("1", "2")])
        problems = Mapping(columns={"date": "no_such_column"}).validate(profile)
        assert any("no_such_column" in p for p in problems)
