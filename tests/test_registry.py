"""Guards for the plan: the refusals page is the product, so it is what is tested."""

from __future__ import annotations

from supportkit.contract import Mapping
from supportkit.registry import DEFAULT_ANALYSES, plan


def _full_mapping() -> Mapping:
    return Mapping(columns={"date": "d", "category": "c", "status": "s",
                            "free_text": "t", "user_id": "u"}, date_parser="iso_like")


class TestGating:
    def test_a_full_mapping_with_good_facts_runs_almost_everything(self) -> None:
        result = plan(_full_mapping(), {"complete_months": 4, "user_id_numeric_share": 1.0},
                      capabilities={"api_key", "labels_sheet"})
        assert [a.name for a in result.runnable] == [a.name for a in DEFAULT_ANALYSES]

    def test_missing_role_refuses_with_a_reason_a_reader_can_act_on(self) -> None:
        result = plan(Mapping(columns={"category": "c"}), {})
        refused = {r.name: r for r in result.refused}
        assert "shift_detection" in refused
        assert any("date" in reason for reason in refused["shift_detection"].reasons)

    def test_predicates_gate_on_measured_facts(self) -> None:
        result = plan(_full_mapping(), {"complete_months": 1, "user_id_numeric_share": 1.0},
                      capabilities={"api_key", "labels_sheet"})
        refused = {r.name: r for r in result.refused}
        assert "shift_detection" in refused
        assert "found 1" in refused["shift_detection"].reasons[0]

    def test_a_messy_id_column_refuses_repeat_contacts(self) -> None:
        result = plan(_full_mapping(), {"complete_months": 4, "user_id_numeric_share": 0.5},
                      capabilities={"api_key", "labels_sheet"})
        refused = {r.name: r for r in result.refused}
        assert "repeat_contacts" in refused
        assert "50%" in refused["repeat_contacts"].reasons[0]

    def test_no_api_key_refuses_the_llm_layer_and_says_nothing_spends(self) -> None:
        result = plan(_full_mapping(), {"complete_months": 4, "user_id_numeric_share": 1.0})
        refused = {r.name: r for r in result.refused}
        assert "wants_taxonomy" in refused
        assert "API key" in refused["wants_taxonomy"].reasons[0]

    def test_every_unmet_requirement_is_listed_not_just_the_first(self) -> None:
        """A reader deciding whether to fix their export needs the whole bill."""
        result = plan(Mapping(), {})
        refused = {r.name: r for r in result.refused}
        assert len(refused["wants_taxonomy"].reasons) == 2  # free_text AND api_key

    def test_profile_always_runs(self) -> None:
        result = plan(Mapping(), {})
        assert [a.name for a in result.runnable] == ["profile"]
