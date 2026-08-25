"""The frame contract: what a ticket-shaped dataset must map onto.

The framework's boundary, stated once: **primitives and enforcement
generalise; loaders and conclusions do not.** Every corpus needs its own
judgment about what a fragment is and what a blank quantity means — so the
framework ships no loader. It ships this contract instead: the roles a mapping
must fill, a proposer that suggests one from *measured* evidence, and a
serialisable ``Mapping`` so that once a human confirms it, next month's export
of the same shape skips the wizard entirely.

The proposer's rule, tested by sabotage: a header called "date" full of junk
loses to a column called "misc" whose values demonstrably parse. Names break
ties; measurements decide.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .dateparse import ParserScore, tournament
from .profiling import SheetProfile

#: The roles an analysis can require. Every one is optional in a mapping —
#: the registry decides what an incomplete mapping still supports.
ROLES = ("date", "category", "status", "free_text", "user_id", "quantity", "operator", "channel")

_HINTS = {
    "date": ("date", "дата", "时间", "受理", "time", "created"),
    "category": ("categor", "категор", "分类", "类型", "type", "тип"),
    "status": ("status", "статус", "状态", "进度", "progress", "state"),
    "free_text": ("problem", "проблем", "вопрос", "咨询问题", "описан", "message", "text", "comment", "内容"),
    "user_id": ("uid", "user_id", "userid", "пользовател", "用户", "client"),
    "quantity": ("qty", "quantity", "кол-во", "数量", "count"),
    "operator": ("operator", "оператор", "接待", "agent", "manager", "менедж"),
    "channel": ("channel", "канал", "渠道", "source", "источник"),
}

#: Below this measured parse rate a column is not a date column, whatever its
#: header says.
DATE_RATE_FLOOR = 0.6
#: Values sampled per column for the parser tournament — enough to measure,
#: cheap enough to run on every column.
TOURNAMENT_SAMPLE = 300


@dataclass
class Mapping:
    """Column-role assignments plus the human rulings that make them usable."""

    columns: dict = field(default_factory=dict)      # role -> column name
    date_parser: str | None = None
    #: Explicit answers to the questions the data forces — e.g.
    #: {"blank_quantity": "not_counted"}. Never guessed; the wizard asks.
    rulings: dict = field(default_factory=dict)

    def role(self, name: str) -> str | None:
        return self.columns.get(name)

    def to_dict(self) -> dict:
        return {"columns": dict(self.columns), "date_parser": self.date_parser,
                "rulings": dict(self.rulings)}

    @classmethod
    def from_dict(cls, data: dict) -> Mapping:
        return cls(columns=dict(data.get("columns") or {}),
                   date_parser=data.get("date_parser"),
                   rulings=dict(data.get("rulings") or {}))

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Mapping:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def validate(self, profile: SheetProfile) -> list[str]:
        """Problems that make this mapping unusable against this profile."""
        problems = []
        for role, column in self.columns.items():
            if role not in ROLES:
                problems.append(f"unknown role {role!r}")
            elif profile.column(column) is None:
                problems.append(f"role {role!r} is mapped to {column!r}, which is not in the sheet")
        return problems


@dataclass
class Proposal:
    mapping: Mapping
    #: role -> one sentence of measured evidence for the choice
    evidence: dict = field(default_factory=dict)
    #: every parser's score on the chosen date column, best first
    date_scores: list = field(default_factory=list)


def _hinted(name: str, role: str) -> bool:
    lowered = name.lower()
    return any(h in lowered for h in _HINTS[role])


def propose_mapping(profile: SheetProfile, values_by_column: dict) -> Proposal:
    """Propose a mapping from measured evidence. A human confirms it; this
    function never gets the last word."""
    mapping = Mapping()
    evidence: dict[str, str] = {}
    taken: set[str] = set()

    # --- date: the tournament decides, names only break ties ----------------
    best: tuple[float, bool, str, ParserScore] | None = None
    date_scores: list[ParserScore] = []
    for col in profile.columns:
        if col.kind in ("numeric", "empty"):
            continue
        values = values_by_column.get(col.name, [])[:TOURNAMENT_SAMPLE]
        scores = tournament(values)
        top = scores[0] if scores else None
        if top is None or top.rate < DATE_RATE_FLOOR or top.parsed < 5:
            continue
        key = (top.rate, _hinted(col.name, "date"), col.name, top)
        if best is None or (key[0], key[1]) > (best[0], best[1]):
            best = key
            date_scores = scores
    if best is not None:
        _, _, column, top = best
        mapping.columns["date"] = column
        mapping.date_parser = top.name
        evidence["date"] = f"measured: {top.evidence()}"
        taken.add(column)

    # --- free text: the widest genuinely-free column ------------------------
    free = [c for c in profile.columns if c.kind == "free text" and c.name not in taken]
    if free:
        chosen = max(free, key=lambda c: (c.cardinality, c.max_len))
        mapping.columns["free_text"] = chosen.name
        evidence["free_text"] = (f"{chosen.cardinality} distinct values, longest {chosen.max_len} chars "
                                 f"— free text by shape")
        taken.add(chosen.name)

    # --- the categorical roles ----------------------------------------------
    def pick(role: str, want_kind: str, max_card: int | None = None, need_hint: bool = False):
        candidates = []
        for col in profile.columns:
            if col.name in taken or col.kind != want_kind:
                continue
            if max_card is not None and col.cardinality > max_card:
                continue
            hinted = _hinted(col.name, role)
            if need_hint and not hinted:
                continue
            candidates.append((hinted, -col.null_rate, col))
        if not candidates:
            return
        hinted, _, col = max(candidates, key=lambda t: (t[0], t[1]))
        mapping.columns[role] = col.name
        why = "header matches" if hinted else "best remaining candidate by shape"
        evidence[role] = f"{col.kind}, {col.cardinality} distinct, {100 * col.null_rate:.1f}% blank — {why}"
        taken.add(col.name)

    pick("status", "categorical", max_card=8, need_hint=True)
    pick("category", "categorical", max_card=60)
    pick("operator", "categorical", max_card=30, need_hint=True)
    pick("channel", "categorical", max_card=15, need_hint=True)
    pick("quantity", "numeric", need_hint=True)

    # --- user id: hint plus id-like shape -----------------------------------
    for col in profile.columns:
        if col.name in taken or not _hinted(col.name, "user_id"):
            continue
        if col.kind in ("numeric", "text") and col.cardinality >= max(10, col.non_null // 4):
            mapping.columns["user_id"] = col.name
            evidence["user_id"] = (f"{col.cardinality} distinct values over {col.non_null} rows — "
                                   f"id-like, header matches")
            taken.add(col.name)
            break

    return Proposal(mapping=mapping, evidence=evidence, date_scores=date_scores)
