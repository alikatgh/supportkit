"""workbook_map decides which sheets get ANALYSED. A wrong call here is not an
error message — it is contacts silently missing from every downstream number.
The workbook this grew from hid 17 contacts (all nine paying users among them)
in two extra sheets a hardcoded name never read.

All fixtures are synthetic. This repository is public; no real support data
may appear in it, ever.
"""

from __future__ import annotations

from supportkit.profiling import profile_sheet
from supportkit.workbook_map import map_workbook, match_column_roles

HEADERS = ("Дата обращения(受理时间)", "Регион(地区)", "Категория(咨询分类)",
           "Проблема(咨询问题)", "Кол-во(数量)", "Статус(处理进度)", "UID")


def contact_rows(n: int = 8) -> list[tuple]:
    rows = [HEADERS]
    for i in range(n):
        rows.append((f"{3 + i % 5} Август (8月{3 + i % 5}日)", "Россия(俄罗斯)",
                     "5 Основные функции(基础功能)",
                     f"почему не работает функция номер {i} 为什么功能{i}不能用",
                     1, "Решено(已解决)", 100000 + i))
    return rows


def test_a_dated_problem_sheet_is_a_contact_log():
    m = map_workbook({"tickets": contact_rows()})
    assert [s.role for s in m.sheets] == ["contact_log"]
    assert m.contact_logs[0].columns["problem"] == "Проблема(咨询问题)"


def test_every_contact_log_is_found_not_just_the_first():
    """The bug this module exists to prevent: extra contact sheets ignored."""
    m = map_workbook({
        "普通用户咨询": contact_rows(20),
        "储值用户咨询": contact_rows(3),
        "Others": contact_rows(2),
    })
    assert len(m.contact_logs) == 3


def test_a_pivot_with_contact_headers_is_not_a_contact_log():
    """A summary sheet copied from a template keeps the headers but holds
    totals where the dates were. Header match alone must not bind it."""
    rows = [HEADERS] + [("итого", "", "5 Основные функции", "все проблемы", 278, "", "")] * 6
    m = map_workbook({"pivot": rows})
    assert m.sheets[0].role == "summary"
    assert "date column does not parse" in m.sheets[0].why


def test_faq_is_detected_by_question_and_answer_headers():
    rows = [("类别", "标准问题（RU）", "标准回复（RU）", "状态")]
    # Canned replies repeat across topics — the answer column must be allowed
    # to look categorical and still count as an answer.
    for i in range(12):
        rows.append(("功能", f"как сделать {i}?", "Здравствуйте! Смотрите настройки.", "待确认"))
    m = map_workbook({"faq": rows})
    assert m.sheets[0].role == "faq"


def test_a_narrow_undated_sheet_is_a_label_sheet():
    rows = [("语义标签",)] + [(f"标签{i}",) for i in range(30)]
    m = map_workbook({"labels": rows})
    assert m.sheets[0].role == "label_sheet"


def test_a_renamed_header_degrades_to_unmatched_never_misbinds():
    profile, body, _ = profile_sheet("s", contact_rows())
    cols = list(profile.columns)
    renamed = [c.__class__(**{**c.__dict__, "name": "Something Else"})
               if c.name.startswith("Проблема") else c for c in cols]
    roles, unmatched = match_column_roles(renamed)
    assert "problem" not in roles
    assert "Something Else" in unmatched


def test_one_column_binds_one_role():
    """Two hint-matching columns must not both claim the same role, and one
    column must not serve two roles."""
    profile, _, _ = profile_sheet("s", contact_rows())
    roles, _ = match_column_roles(list(profile.columns))
    headers = list(roles.values())
    assert len(headers) == len(set(headers))
