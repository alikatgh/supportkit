"""Date parsing as a measured tournament, never a guess.

The rule this module encodes: **a column is a date column because a parser
demonstrably parses it, not because its header says "date".** The wizard and
the CLI show each candidate parser's measured parse rate on the actual values,
and the winner wins on the numbers.

A parser returns *every* reading it can defend for one value. Two distinct
readings from a single cell is a **self-conflict** — a bilingual date whose
two halves name different days (`6 апрель(4月4日)`), or an ambiguous
`03/04/2026` that is defensible as both April 3rd and March 4th. Conflicts are
counted and surfaced, never resolved silently: on one real corpus, 11 rows
disagreed with themselves and the honest answer was a human ruling, not a
coin-flip in a parser.

Absence of a year is likewise reported, not papered over. A month/day-only
column supports month-level work and nothing across a year boundary.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Reading:
    """One defensible interpretation of a date value."""

    month: int
    day: int
    year: int | None = None


ParserFn = Callable[[object], list[Reading]]


@dataclass(frozen=True)
class Parser:
    name: str
    parse: ParserFn
    description: str


# ---------------------------------------------------------------------------
# Bilingual Russian/Chinese: "9 марта(3月9日)", "3 Август (8月3日)"
# ---------------------------------------------------------------------------

# Iteration order is load-bearing: "март" must be tested before "ма", or
# "9 марта" reads as May. A guard test pins this; reordering the table is the
# known sabotage that turns it red.
RU_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
_RU_DATE = re.compile(r"(\d{1,2})\s*([А-Яа-яЁё]+)")
_ZH_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def parse_ru_half(text: str) -> Reading | None:
    """The Russian half alone: day + declined month word."""
    match = _RU_DATE.search(str(text))
    if not match:
        return None
    day, word = int(match.group(1)), match.group(2).lower()
    for stem, month in RU_MONTHS.items():
        if word.startswith(stem):
            return Reading(month=month, day=day)
    return None


def parse_zh_half(text: str) -> Reading | None:
    """The Chinese half alone: numeric month and day."""
    match = _ZH_DATE.search(str(text))
    if not match:
        return None
    return Reading(month=int(match.group(1)), day=int(match.group(2)))


def _ru_zh(value: object) -> list[Reading]:
    text = str(value)
    year_match = _YEAR.search(text)
    year = int(year_match.group(1)) if year_match else None
    readings = []
    for half in (parse_ru_half(text), parse_zh_half(text)):
        if half is None:
            continue
        reading = Reading(month=half.month, day=half.day, year=year)
        if reading not in readings:
            readings.append(reading)
    return readings


# ---------------------------------------------------------------------------
# ISO-ish and numeric forms, including real datetime cells from openpyxl
# ---------------------------------------------------------------------------

_UNAMBIGUOUS_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")
_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _iso_like(value: object) -> list[Reading]:
    if isinstance(value, datetime):
        return [Reading(month=value.month, day=value.day, year=value.year)]
    if isinstance(value, date):
        return [Reading(month=value.month, day=value.day, year=value.year)]
    text = str(value).strip()
    if not text:
        return []
    for fmt in _UNAMBIGUOUS_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return [Reading(month=parsed.month, day=parsed.day, year=parsed.year)]
        except ValueError:
            continue
    slash = _SLASH.match(text)
    if slash:
        a, b, year = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        readings = []
        # Both orders that survive validation are returned. 03/04/2026 is
        # defensible as either date, and that ambiguity IS a self-conflict —
        # the measured conflict count is how the wizard learns a column needs
        # a locale ruling rather than a silent default.
        if 1 <= b <= 12 and 1 <= a <= 31:
            readings.append(Reading(month=b, day=a, year=year))
        if 1 <= a <= 12 and 1 <= b <= 31:
            reading = Reading(month=a, day=b, year=year)
            if reading not in readings:
                readings.append(reading)
        return readings
    return []


PARSERS: tuple[Parser, ...] = (
    Parser("ru_zh_bilingual", _ru_zh,
           "Bilingual Russian/Chinese dates like '9 марта(3月9日)'; both halves read independently"),
    Parser("iso_like", _iso_like,
           "ISO and numeric dates, including real datetime cells; ambiguous d/m/y surfaces as a conflict"),
)


def get_parser(name: str) -> Parser:
    for parser in PARSERS:
        if parser.name == name:
            return parser
    raise KeyError(f"no such date parser: {name!r}")


@dataclass(frozen=True)
class ParserScore:
    """One parser's measured performance on one column's actual values."""

    name: str
    total: int
    parsed: int
    conflicts: int
    with_year: int

    @property
    def rate(self) -> float:
        return self.parsed / self.total if self.total else 0.0

    def evidence(self) -> str:
        bits = [f"{self.name} parses {self.parsed}/{self.total} ({100 * self.rate:.1f}%)"]
        if self.conflicts:
            bits.append(f"{self.conflicts} self-conflicts")
        if self.parsed and self.with_year == 0:
            bits.append("no value carries a year")
        return ", ".join(bits)


def evaluate(parser: Parser, values: Iterable[object]) -> ParserScore:
    total = parsed = conflicts = with_year = 0
    for value in values:
        if value is None or not str(value).strip():
            continue
        total += 1
        readings = parser.parse(value)
        if not readings:
            continue
        parsed += 1
        if len({(r.month, r.day) for r in readings}) > 1:
            conflicts += 1
        if any(r.year is not None for r in readings):
            with_year += 1
    return ParserScore(name=parser.name, total=total, parsed=parsed,
                       conflicts=conflicts, with_year=with_year)


def tournament(values: Iterable[object], parsers: tuple[Parser, ...] = PARSERS) -> list[ParserScore]:
    """Every parser scored on the same values, best rate first.

    The caller picks by the numbers. Name hints may break ties; they must
    never beat a measured rate — that inversion is the tested failure mode.
    """
    materialised = list(values)
    scores = [evaluate(parser, materialised) for parser in parsers]
    return sorted(scores, key=lambda s: (s.rate, s.parsed), reverse=True)
