#!/usr/bin/env python3
"""Pseudonymize personal data in ticket text before it leaves the machine.

Why
---

The projects this grew out of treat every export, output, and ticket example
as private. That stance held while extraction ran locally through Ollama — nothing
crossed the network. Then a real batch of tickets was read through the paid
DeepSeek API with their raw text intact: UIDs, phone numbers, profile URLs,
e-mail addresses, and whatever a distressed user pasted into a support form.
The privacy claim and the code stopped agreeing.

This module makes them agree again. It does not try to make the text
un-analysable — it replaces *values* while preserving *kinds*, so the model can
still reason about structure ("the user gave a UID and a screenshot URL")
without receiving the identifiers themselves.

Design decisions
----------------

**Pseudonyms, not redaction.** Every distinct value maps to a stable numbered
placeholder within one scrubbing scope: ``<UID_1>``, ``<URL_2>``. A ticket
saying "my account 88231 was banned but 88231 is not the one that broke the
rules" keeps the fact that both mentions are the *same* account — information
plain ``[REDACTED]`` would destroy, and which the extractor genuinely uses.

**Typed placeholders.** ``<PHONE_1>`` tells the model a phone number was
present. The evidence-scoring features in the pipeline count exactly this kind
of signal, so preserving the type keeps extraction quality intact.

**Order matters.** URLs are matched before e-mails and numbers, because a
profile URL contains both; matching numbers first would shred the URL into
fragments and leave the domain exposed.

**Conservative on free digits.** Only digit runs of 5+ are treated as
identifiers. Scrubbing every "3" would destroy counts, dates, and money amounts
that the analysis depends on.

Scope of the guarantee
----------------------

This is a strong reduction of exposure, not a proof of anonymity. Free text can
always carry an identifier no pattern predicts ("I am the admin of the Tashkent
sellers group"). Treat it as defence in depth alongside the standing rule that
paid extraction is opt-in — not as a licence to publish raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["Scrubber", "scrub_text", "scrub_stats"]

# Ordered: the first pattern to match a span wins. URL before EMAIL before the
# numeric identifiers, so a profile link is replaced whole.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("URL", re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.I)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # International and local phone shapes: optional +, then digits with spaces,
    # dashes, dots, or parens mixed in. Deliberately loose here and validated in
    # _is_plausible_phone — a regex tight enough to exclude dates and prices is
    # unreadable, and getting it wrong silently deletes analysis signal.
    ("PHONE", re.compile(r"(?<![\w.])\(?\+?\d[\d\s().-]{5,18}\d\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Real octets only. `\d{1,3}` claimed dotted phone numbers too — an Uzbek
    # mobile written "8.916.123.45.67" came back as <IP_1>. It was still
    # scrubbed, so nothing leaked, but the placeholder TYPE is deliberate
    # signal for the model, and telling it a phone number is an IP address is
    # the one thing typed placeholders exist to avoid.
    ("IP", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")),
    ("HANDLE", re.compile(r"(?<![\w./])@[A-Za-z][\w.]{2,31}\b")),
    # Identifier runs: user IDs, room IDs, case numbers. 5+ digits only —
    # shorter runs are counts, prices, and dates the analysis needs.
    #
    # The guard used to be (?<![\w.]) ... (?![\w.]), which refused any digit run
    # touching a letter or underscore. That let the three commonest real shapes
    # through untouched: "UID12345678" — this corpus's OWN identifier format —
    # plus "12345678x" and "+7_999_1234567". An identifier glued to a prefix is
    # still an identifier.
    #
    # What the guard actually has to protect is DECIMALS: "1234567.89" is money
    # the analysis reads, and scrubbing either half destroys it. So refuse only
    # true decimal context — a neighbouring digit, or a dot with a digit on its
    # far side — and let letters and underscores through. A trailing sentence
    # period ("my id is 123456.") no longer blocks the match either.
    ("ID", re.compile(r"(?<!\d)(?<!\d\.)\d{5,}(?!\d)(?!\.\d)")),
]

# CARD must be tried before PHONE (a 16-digit card matches the phone shape too),
# and IP before ID. Re-order the working list accordingly while keeping the
# table above readable.
_ORDER = ["URL", "EMAIL", "IP", "CARD", "PHONE", "HANDLE", "ID"]
_BY_KIND = dict(PATTERNS)
ORDERED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [(k, _BY_KIND[k]) for k in _ORDER]

# Dates are analysis signal, not PII, and they look exactly like phone numbers
# to a loose pattern: "03.04.2026" is ten digits with separators. Scrubbing them
# would quietly strip the timestamps the evidence-quality score counts.
DATE_LIKE = re.compile(
    r"^\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})\s*$"
)
# Clock times ("12:30") never reach the phone pattern (no colon in its class),
# but decimal money amounts can: "1 234.56".
#
# The integer part is \d+, not \d{1,3}: requiring grouped thousands meant an
# unseparated amount like "1234567.89" failed this test and was scrubbed as a
# phone number, deleting the figure from the ticket. That is not a corner case
# for this corpus — sums run to seven digits and are routinely typed without
# separators. A real phone number is never a single dot-decimal with one or
# two trailing digits, so widening here costs no coverage.
DECIMAL_LIKE = re.compile(r"^\s*\d+(?:[ ,]\d{3})*\.\d{1,2}\s*$")

MIN_PHONE_DIGITS = 7
MAX_PHONE_DIGITS = 15


def _digit_count(value: str) -> int:
    return sum(1 for ch in value if ch.isdigit())


def _is_plausible_phone(value: str) -> bool:
    """True when a loose phone match is really a phone number.

    Rejects dates, decimal amounts, and digit runs outside the ITU-plausible
    7-15 range. A false positive here is not harmless: it deletes a date or a
    price from the ticket text the model is asked to interpret.
    """
    if DATE_LIKE.match(value) or DECIMAL_LIKE.match(value):
        return False
    digits = _digit_count(value)
    if not MIN_PHONE_DIGITS <= digits <= MAX_PHONE_DIGITS:
        return False
    # A bare run of digits with no "+" and no separators is far more likely an
    # account/room/case ID than a phone number, and this corpus is full of them.
    # Let short bare runs fall through to the ID rule so the placeholder type
    # stays informative. Either way the value is still replaced.
    bare = not any(ch in value for ch in "+ ().-")
    return digits >= 10 if bare else True


def _is_plausible_card(value: str) -> bool:
    return not DATE_LIKE.match(value) and 13 <= _digit_count(value) <= 19


# Extra acceptance tests, applied after the regex matches. A kind with no entry
# is accepted on the pattern alone.
VALIDATORS = {
    "PHONE": _is_plausible_phone,
    "CARD": _is_plausible_card,
}


@dataclass
class Scrubber:
    """Stateful pseudonymizer with a stable value → placeholder mapping.

    Reusing one ``Scrubber`` across a whole extraction run keeps the mapping
    consistent between tickets, so the same UID reads as ``<UID_7>`` everywhere
    and cross-ticket reasoning survives. Create a fresh one per ticket if you
    want each ticket independently unlinkable.

    The mapping stays in memory only. Persisting it would recreate the exposure
    this module exists to prevent.
    """

    mapping: dict[tuple[str, str], str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def placeholder(self, kind: str, value: str) -> str:
        """Return the stable placeholder for one (kind, value) pair."""
        key = (kind, value)
        existing = self.mapping.get(key)
        if existing is not None:
            return existing
        index = self.counts.get(kind, 0) + 1
        self.counts[kind] = index
        token = f"<{kind}_{index}>"
        self.mapping[key] = token
        return token

    def scrub(self, text: object) -> str:
        """Replace every recognised identifier in ``text`` with a placeholder."""
        if text is None:
            return ""
        out = str(text)
        if not out.strip():
            return out
        for kind, pattern in ORDERED_PATTERNS:
            out = pattern.sub(lambda m, k=kind: self._replace(k, m.group(0)), out)
        return out

    def _replace(self, kind: str, matched: str) -> str:
        """Map one match, preserving the whitespace it was padded with.

        The PHONE pattern deliberately allows internal spaces and punctuation,
        so a match can carry leading/trailing separators that belong to the
        sentence. Trimming them back out keeps the text readable.
        """
        stripped = matched.strip()
        prefix = matched[: len(matched) - len(matched.lstrip())]
        suffix = matched[len(matched.rstrip()):]
        if not stripped:
            return matched
        validator = VALIDATORS.get(kind)
        if validator and not validator(stripped):
            return matched  # a date or an amount, not an identifier
        return f"{prefix}{self.placeholder(kind, stripped.lower())}{suffix}"

    def summary(self) -> dict[str, int]:
        """How many distinct values were replaced, per kind."""
        return dict(sorted(self.counts.items()))


def scrub_text(text: object, scrubber: Scrubber | None = None) -> str:
    """Scrub one string. Convenience wrapper for one-off use."""
    return (scrubber or Scrubber()).scrub(text)


def scrub_stats(text: object) -> dict[str, int]:
    """Count the identifiers a string contains, without keeping the values.

    Useful for auditing an export ("how much PII would leave the machine if we
    sent this?") without producing a mapping anyone could leak.
    """
    scrubber = Scrubber()
    scrubber.scrub(text)
    return scrubber.summary()


def main() -> int:
    """CLI: report the PII profile of a column in an export, without printing it."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Audit how much PII a text column contains.")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--column", default="question", help="Text column to audit")
    parser.add_argument("--sample", type=int, default=0, help="Print N scrubbed examples (safe to share)")
    args = parser.parse_args()

    import csv as csv_module

    totals: dict[str, int] = {}
    rows = 0
    shown = 0
    with args.csv.open("r", encoding="utf-8", newline="") as handle:
        for row in csv_module.DictReader(handle):
            value = row.get(args.column, "")
            if not value:
                continue
            rows += 1
            scrubber = Scrubber()
            scrubbed = scrubber.scrub(value)
            for kind, count in scrubber.summary().items():
                totals[kind] = totals.get(kind, 0) + count
            if shown < args.sample and scrubber.summary():
                print(f"  {scrubbed[:300]}")
                shown += 1
    print(f"rows with text: {rows:,}")
    for kind, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {kind}: {count:,} identifier(s) across the corpus")
    if not totals:
        print("  no recognised identifiers found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
