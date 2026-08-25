"""Profile an unknown sheet before anyone draws a conclusion from it.

The order is the doctrine, learned expensively twice: **profile first, decide
what the data can answer, only then analyse.** Both parent projects had a
predecessor stage rebuilt because the pipeline shape was guessed before the
data was read.

Everything here works on plain rows (tuples from openpyxl or csv) — no pandas,
no UI import. Sample values shown in any report pass through the same
pseudonymising chokepoint as everything else (`pii.Scrubber`): a profile is a
derived file, and gitignoring the input does not protect what you generate
from it.
"""

from __future__ import annotations

import calendar
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .pii import Scrubber

MAX_HEADER_SCAN = 6

#: Rows that are instructions to the reader, not records. Hand-maintained
#: sheets contain "the rows below are examples" banners in whatever language
#: the team writes; they carry values in other columns and inflate every count.
MARKER_KEYWORDS = ("示例", "模板", "example", "template", "образец", "пример")

# Deliberately broad: these flag columns for a human to look at. pii.Scrubber
# is the scrubber; this is only the smoke detector.
PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[\s-]?){9,15}(?!\d)"),
    "url": re.compile(r"https?://\S+"),
    "at_handle": re.compile(r"(?<!\w)@[A-Za-z][\w.]{2,}"),
}
PII_HEADER_HINTS = ("uid", "id", "user", "оператор", "接待人", "имя", "name", "телефон", "почт", "email", "phone")


def blank(value: object) -> bool:
    return value is None or not str(value).strip()


def find_header_row(rows: Sequence[tuple], max_scan: int = MAX_HEADER_SCAN) -> int:
    """Index of the row that looks most like a header.

    Summary sheets open with a one-cell banner and put the real header two or
    three rows down. Scoring by distinct non-blank cells picks the header
    without hardcoding a row number, which would rot on the first inserted row.
    """
    best, best_score = 0, -1
    for i, row in enumerate(rows[:max_scan]):
        cells = [str(c).strip() for c in row if not blank(c)]
        score = len(set(cells))
        if score > best_score:
            best, best_score = i, score
    return best


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    index: int
    n: int
    non_null: int
    cardinality: int
    kind: str            # empty | numeric | categorical | free text | text
    max_len: int
    top: tuple           # ((scrubbed value, count), ...) — never raw cell text
    pii_patterns: dict
    pii_header: bool

    @property
    def null_rate(self) -> float:
        return 1 - self.non_null / self.n if self.n else 1.0


def classify_column(name: str, values: Sequence[object], scrubber: Scrubber | None = None) -> ColumnProfile:
    """Null rate, cardinality, kind and PII flags for one column.

    ``scrubber`` is shared across a report so the same identifier reads as the
    same placeholder everywhere — a fresh scrubber per column once rendered
    three distinct UIDs as ``<ID_1>`` each, which reads as one UID counted
    three times.
    """
    scrubber = scrubber or Scrubber()
    non_null = [v for v in values if not blank(v)]
    texts = [str(v).strip() for v in non_null]
    counts = Counter(texts)
    numeric = sum(1 for t in texts if re.fullmatch(r"-?\d+(\.\d+)?", t))
    lengths = [len(t) for t in texts] or [0]
    hits = {k: sum(1 for t in texts if p.search(t)) for k, p in PII_PATTERNS.items()}
    hits = {k: v for k, v in hits.items() if v}
    if not texts:
        kind = "empty"
    elif numeric == len(texts):
        kind = "numeric"
    elif len(counts) <= max(20, len(texts) // 20):
        kind = "categorical"
    elif max(lengths) > 80:
        kind = "free text"
    else:
        kind = "text"
    return ColumnProfile(
        name=name, index=0, n=len(values), non_null=len(non_null),
        cardinality=len(counts), kind=kind, max_len=max(lengths),
        top=tuple((scrubber.scrub(v), c) for v, c in counts.most_common(5)),
        pii_patterns=hits,
        pii_header=any(h in name.lower() for h in PII_HEADER_HINTS),
    )


@dataclass
class SheetProfile:
    sheet: str
    header_row: int                  # 1-based, as a human reads a spreadsheet
    data_rows: int
    dupe_rows: int
    marker_rows: tuple               # 1-based row numbers of instruction rows
    columns: list = field(default_factory=list)

    def column(self, name: str) -> ColumnProfile | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None


def profile_sheet(sheet: str, rows: Sequence[tuple]) -> tuple[SheetProfile, list[tuple], list[str]]:
    """Profile one sheet. Returns (profile, body_rows, headers).

    Body rows exclude blanks and marker rows — an instruction row is not a
    record, and it must be dropped *before* counting, not after a wrong total
    ships.
    """
    if not rows:
        return SheetProfile(sheet=sheet, header_row=1, data_rows=0, dupe_rows=0, marker_rows=()), [], []
    header_idx = find_header_row(rows)
    headers = [
        (str(c).strip() if not blank(c) else f"<unnamed col {i + 1}>")
        for i, c in enumerate(rows[header_idx])
    ]
    body, markers = [], []
    for offset, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if all(blank(c) for c in row):
            continue
        if any(k in str(c) for c in row for k in MARKER_KEYWORDS if not blank(c)):
            filled = sum(1 for c in row if not blank(c))
            if filled <= 3:
                markers.append(offset)
                continue
        body.append(row)

    used = [i for i in range(len(headers)) if any(not blank(r[i]) if i < len(r) else False for r in body)]
    scrubber = Scrubber()
    columns = []
    for i in used:
        values = [r[i] if i < len(r) else None for r in body]
        col = classify_column(headers[i], values, scrubber)
        columns.append(ColumnProfile(**{**col.__dict__, "index": i}))
    signatures = [tuple("" if blank(r[i]) else str(r[i]).strip() for i in used) for r in body]
    profile = SheetProfile(
        sheet=sheet, header_row=header_idx + 1, data_rows=len(body),
        dupe_rows=len(signatures) - len(set(signatures)), marker_rows=tuple(markers),
        columns=columns,
    )
    return profile, body, headers


def column_values(body: Sequence[tuple], index: int) -> list[object]:
    return [r[index] if index < len(r) else None for r in body]


@dataclass(frozen=True)
class MonthCoverage:
    """Whether one month's observed days span the whole calendar month.

    The bug this exists to catch: a raw monthly chart compared a 20-day window
    against 31-day ones and showed a fall that was not there — per active day
    the "falling" month was the busiest in the file. Check every time bucket
    covers the same span before reading any trend.
    """

    month: int
    first_day: int
    last_day: int
    days_in_month: int
    contacts: int

    @property
    def partial(self) -> bool:
        return self.first_day > 1 or self.last_day < self.days_in_month

    @property
    def days_covered(self) -> int:
        return self.last_day - self.first_day + 1 if self.partial else self.days_in_month

    @property
    def per_day(self) -> float:
        return self.contacts / self.days_covered if self.days_covered else 0.0


def month_coverage(month_days: Iterable[tuple[int, int]], reference_year: int) -> list[MonthCoverage]:
    """Coverage per month from (month, day) pairs.

    ``reference_year`` matters only for February's length; corpora without a
    year on the date (they exist) must pass an explicit assumption rather than
    having one silently made for them.
    """
    by_month: dict[int, list[int]] = {}
    for month, day in month_days:
        by_month.setdefault(int(month), []).append(int(day))
    out = []
    for month in sorted(by_month):
        days = by_month[month]
        out.append(MonthCoverage(
            month=month, first_day=min(days), last_day=max(days),
            days_in_month=calendar.monthrange(reference_year, month)[1],
            contacts=len(days),
        ))
    return out
