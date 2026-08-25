"""Analyses declare what they need; the engine says what your data supports.

The differentiating output is the second list. Every tool will run what it
can; this one also generates **the refusals page** — what your data cannot
answer, with the measured reason — from the same declarations. "What this
data cannot support" stops being a hand-written document and becomes an
output of the contract.

The pattern is a generalisation of a 13-stage production orchestrator whose
every stage declared ``requires=(...)`` and an output sentinel. Here the
declarations gate *whether an analysis is offered at all*, and the reasons are
written for the person reading the page, not for a log file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .contract import Mapping

# Predicates are measured facts about the mapped data, computed by the caller
# (the CLI, later the wizard). Each carries the sentence shown when it fails —
# a refusal without its reason teaches the reader nothing.
PREDICATES = {
    "two_complete_months": (
        lambda facts: facts.get("complete_months", 0) >= 2,
        lambda facts: (f"needs at least two COMPLETE months of dated contacts; found "
                       f"{facts.get('complete_months', 0)} (partial months are excluded — a short "
                       f"window reads as a collapse in volume and a fake share shift)"),
    ),
    "stable_user_id": (
        lambda facts: facts.get("user_id_numeric_share", 0.0) >= 0.9,
        lambda facts: (f"needs a stable user identifier; only "
                       f"{100 * facts.get('user_id_numeric_share', 0.0):.0f}% of id cells are "
                       f"uniform — the column mixes id types, so one row is not one user"),
    ),
}

_ROLE_REASONS = {
    "date": "no column is mapped to the date role",
    "category": "no column is mapped to the category role",
    "status": "no column is mapped to the status role",
    "free_text": "no free-text column is mapped — there is nothing to read",
    "user_id": "no column is mapped to the user-id role",
    "quantity": "no quantity column is mapped",
}

_CAPABILITY_REASONS = {
    "api_key": ("needs an API key — none provided. Nothing spends without one, and every spend "
                "shows its estimated cost before a call is made"),
    "labels_sheet": "needs a second sheet of labels (an FAQ or taxonomy sheet) mapped alongside the log",
}


@dataclass(frozen=True)
class Analysis:
    name: str
    title: str
    requires_roles: tuple = ()
    predicates: tuple = ()
    capabilities: tuple = ()


@dataclass(frozen=True)
class Refusal:
    name: str
    title: str
    reasons: tuple

    def sentence(self) -> str:
        return f"{self.title}: " + "; ".join(self.reasons)


@dataclass
class Plan:
    runnable: list = field(default_factory=list)     # [Analysis]
    refused: list = field(default_factory=list)      # [Refusal]


DEFAULT_ANALYSES: tuple[Analysis, ...] = (
    Analysis("profile", "Profile, defects and open decisions"),
    Analysis("category_ranking", "What users contact support about",
             requires_roles=("category",)),
    Analysis("resolution_backlog", "Where the unresolved backlog is (Wilson intervals)",
             requires_roles=("category", "status")),
    Analysis("monthly_volume", "Contact volume per month, corrected for partial months",
             requires_roles=("date",)),
    Analysis("shift_detection", "What changed — volume z AND share test, both required",
             requires_roles=("date", "category"), predicates=("two_complete_months",)),
    Analysis("faq_coverage", "Which demand has an answer, which has none",
             requires_roles=("category",), capabilities=("labels_sheet",)),
    Analysis("repeat_contacts", "Who comes back — repeat-contact journeys",
             requires_roles=("user_id",), predicates=("stable_user_id",)),
    Analysis("wants_taxonomy", "What users actually want (model-read, open vocabulary)",
             requires_roles=("free_text",), capabilities=("api_key",)),
)


def plan(mapping: Mapping, facts: dict, capabilities: set | None = None,
         analyses: tuple = DEFAULT_ANALYSES) -> Plan:
    """Split the analyses into runnable and refused-with-reasons.

    An analysis is refused for *every* unmet requirement, not just the first —
    a reader deciding whether to fix their export needs the whole bill.
    """
    capabilities = capabilities or set()
    result = Plan()
    for analysis in analyses:
        reasons = []
        for role in analysis.requires_roles:
            if not mapping.role(role):
                reasons.append(_ROLE_REASONS.get(role, f"no column is mapped to the {role} role"))
        for predicate in analysis.predicates:
            check, describe = PREDICATES[predicate]
            # A predicate over an unmapped role would double-report; only
            # measured facts on data that exists get a say.
            if not reasons and not check(facts):
                reasons.append(describe(facts))
        for capability in analysis.capabilities:
            if capability not in capabilities:
                reasons.append(_CAPABILITY_REASONS.get(capability, f"needs {capability}"))
        if reasons:
            result.refused.append(Refusal(analysis.name, analysis.title, tuple(reasons)))
        else:
            result.runnable.append(analysis)
    return result
