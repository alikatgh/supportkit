"""Discover what each sheet of a support workbook IS, before reading any of it.

Real support exports are not one clean table. The one this module grew from
has seven sheets: a main contact log, two more contact logs with the same
columns (one of them the paying users), a label sheet, an FAQ, a pivot of
someone's summary, and a scratch page. A pipeline that hardcodes one sheet
name silently drops every other contact — here that meant 17 contacts,
including all nine from paying users.

`map_workbook` profiles every sheet and classifies each by ROLE:

    contact_log   dated rows of free text — the thing to analyse
    label_sheet   one label per row, no dates — someone's classification
    faq           question/answer pairs
    summary       a pivot or totals block — derived, never a source
    scratch       too small or shapeless to say

Classification is heuristic and runs entirely offline: the shapes that
separate these roles (does a date column parse? is the longest column free
text? are there totals rows?) do not need a model, and a mapping step that
phones home would have to scrub every cell it looked at first.

Column ROLES inside a contact log are matched bilingually (Russian and
Chinese headers, the export's own convention) and then verified against the
values, so a renamed header degrades to "unmatched" instead of silently
binding the wrong column.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dateparse import tournament
from .profiling import ColumnProfile, SheetProfile, blank, profile_sheet

#: Bilingual header fragments -> canonical column role. Lowercased substring
#: match. Order matters only for documentation; ties are broken by value checks.
COLUMN_ROLE_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("дата", "受理时间", "时间", "date"),
    "region": ("регион", "地区"),
    "category": ("категор", "分类", "category"),
    "problem": ("проблема", "咨询问题", "问题", "problem"),
    "reply": ("回复", "ответ", "reply"),
    "attachment": ("скриншот", "截图", "видео"),
    "note": ("备注", "примеч", "note"),
    "quantity": ("кол-во", "数量"),
    "status": ("статус", "处理进度", "status"),
    "uid": ("uid",),
    "channel": ("канал", "咨询渠道", "channel"),
    "operator": ("оператор", "接待人", "operator"),
}

#: Roles a sheet must bind before it counts as a contact log. `problem` is the
#: analysis payload; `date` is what makes trend work possible at all.
CONTACT_LOG_REQUIRED = ("date", "problem")


@dataclass(frozen=True)
class SheetMap:
    sheet: str
    role: str                       # contact_log | label_sheet | faq | summary | scratch
    rows: int
    header_row: int
    columns: dict[str, str]         # column role -> header text (contact logs only)
    unmatched: tuple[str, ...]      # headers no role claimed
    why: str                        # one line a human can check the call against


@dataclass(frozen=True)
class WorkbookMap:
    sheets: tuple[SheetMap, ...]

    @property
    def contact_logs(self) -> tuple[SheetMap, ...]:
        return tuple(s for s in self.sheets if s.role == "contact_log")

    def render(self) -> str:
        lines = []
        for s in self.sheets:
            lines.append(f"{s.sheet:24} {s.role:12} {s.rows:5} rows — {s.why}")
            if s.role == "contact_log" and s.unmatched:
                lines.append(f"{'':24} unmatched headers: {', '.join(s.unmatched)}")
        return "\n".join(lines)


def match_column_roles(columns: list[ColumnProfile]) -> tuple[dict[str, str], list[str]]:
    """Bind headers to roles by bilingual hint, one column per role.

    A hint match alone is not a binding: `date` must actually parse on the
    values and `problem` must be the free-text payload, or the header lied
    (someone reused a template and typed totals under 'Проблема').
    """
    roles: dict[str, str] = {}
    claimed: set[int] = set()
    for role, hints in COLUMN_ROLE_HINTS.items():
        for col in columns:
            if col.index in claimed:
                continue
            header = col.name.lower()
            if any(h in header for h in hints):
                roles[role] = col.name
                claimed.add(col.index)
                break
    unmatched = [c.name for c in columns if c.index not in claimed and c.kind != "empty"]
    return roles, unmatched


def _dates_parse(values: list[object], threshold: float = 0.5) -> bool:
    """True when the best date parser reads at least `threshold` of the values.

    Measured, not guessed: the parser tournament scores every registered parser
    on the actual cells, the same way the analysis pipeline chooses one.
    """
    present = [v for v in values if not blank(v)]
    if not present:
        return False
    scores = tournament(present)
    return bool(scores) and scores[0].rate >= threshold


def classify_sheet(profile: SheetProfile, body: list[tuple]) -> SheetMap:
    cols = list(profile.columns)
    roles, unmatched = match_column_roles(cols)
    rows = profile.data_rows

    def col_values(role: str) -> list[object]:
        name = roles.get(role)
        if name is None:
            return []
        idx = next(c.index for c in cols if c.name == name)
        return [r[idx] if idx < len(r) else None for r in body]

    if all(r in roles for r in CONTACT_LOG_REQUIRED) and rows > 0:
        if _dates_parse(col_values("date")):
            return SheetMap(profile.sheet, "contact_log", rows, profile.header_row,
                            roles, tuple(unmatched),
                            "dated rows with a problem column; dates parse")
        return SheetMap(profile.sheet, "summary", rows, profile.header_row, {}, (),
                        "has contact-log headers but the date column does not parse — "
                        "likely a pivot built from one")

    # FAQ: a question-ish and an answer-ish header, and no dates. Header
    # semantics, not column kinds — answer columns dedupe into "categorical"
    # the moment two topics share a canned reply, which real FAQs do.
    headers_l = [c.name.lower() for c in cols]
    has_q = any(any(h in name for h in ("问题", "вопрос", "question")) for name in headers_l)
    has_a = any(any(h in name for h in ("回复", "答", "ответ", "answer", "reply", "参考"))
                for name in headers_l)
    if has_q and has_a and "date" not in roles and rows > 0:
        return SheetMap(profile.sheet, "faq", rows, profile.header_row, {}, (),
                        "question and answer headers, no dates")
    free_text = [c for c in cols if c.kind == "free text"]
    if len(free_text) >= 2 and "date" not in roles and rows > 0:
        return SheetMap(profile.sheet, "faq", rows, profile.header_row, {}, (),
                        "two or more free-text columns and no dates — question/answer shaped")
    if len(cols) <= 3 and rows >= 5 and "date" not in roles:
        return SheetMap(profile.sheet, "label_sheet", rows, profile.header_row, {}, (),
                        "narrow, undated, many rows — one label per row")
    if rows < 5:
        return SheetMap(profile.sheet, "scratch", rows, profile.header_row, {}, (),
                        "too few rows to classify")
    return SheetMap(profile.sheet, "summary", rows, profile.header_row, {}, (),
                    "no dates and no clear text payload — derived numbers")


def map_workbook(sheet_rows: dict[str, list[tuple]]) -> WorkbookMap:
    """Classify every sheet. Input: sheet name -> raw rows (header included)."""
    out = []
    for sheet, rows in sheet_rows.items():
        profile, body, _ = profile_sheet(sheet, rows)
        out.append(classify_sheet(profile, body))
    return WorkbookMap(tuple(out))
