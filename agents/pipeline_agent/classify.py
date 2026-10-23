"""Cheap, deterministic ticket classifier (no LLM).

The class drives per-type budgets in the manager (WS-5.3): step cap, review
model, and how much verification the worker does. A "create one picklist
field" ticket does not need a 20-turn budget or a `terra` review.

Types:
- ``metadata-single`` — one declarative component (a field, a layout tweak, a
  permission set, a list view). Smallest budget.
- ``metadata``        — several declarative components, still no Apex/JS.
- ``apex``            — Apex classes / triggers / tests.
- ``lwc``             — a Lightning Web Component (its JS/HTML + controller).
- ``flow``            — a Flow / Process Builder.
- ``mixed``           — Apex + LWC, or anything spanning several kinds.
"""

from __future__ import annotations

import re

TicketType = str

_APEX_RE = re.compile(r"\bapex\b|\btrigger\b|\.cls\b|\bSOQL\b|@AuraEnabled|@IsTest", re.I)
_LWC_RE = re.compile(r"\blwc\b|lightning web component|\.js-meta\.xml|lightning__|wire adapter", re.I)
_FLOW_RE = re.compile(r"\bflow\b|process builder|record-triggered|\.flow-meta\.xml", re.I)
_METADATA_RE = re.compile(
    r"custom field|picklist|page layout|permission set|field-level security|\bFLS\b|"
    r"list view|record type|validation rule|custom label|tab\b|custom object",
    re.I,
)
# phrases that say "one component"
_SINGLE_HINT_RE = re.compile(
    r"\b(a|one|single) (custom |new )?(field|picklist|layout|permission set|list ?view|"
    r"record type|validation rule|tab|label)\b",
    re.I,
)
# phrases that say "more than one"
_PLURAL_HINT_RE = re.compile(
    r"\b(several|multiple|fields|layouts|permission sets|list views|record types|"
    r"validation rules|and (a |an |the )?\w+ (field|layout|permission set|rule|view|object))\b",
    re.I,
)


# "without changing … Flow, sharing, or code", "no Apex needed" — negated
# mentions were pushing metadata tickets to `mixed` (run df1198c7).
_NEGATION_RE = re.compile(r"\b(no|not|without|never|avoid|exclude|zero)\b[^.\n]{0,40}$", re.I)


def _mentions(pattern: re.Pattern, text: str) -> bool:
    for m in pattern.finditer(text):
        prefix = text[max(0, m.start() - 45) : m.start()]
        if _NEGATION_RE.search(prefix):
            continue  # "no Apex", "without a Flow"
        return True
    return False


def classify_ticket(ticket_markdown: str, plan_text: str | None = None) -> TicketType:
    """Best-effort. `plan_text` (the approved plan) is the stronger signal when
    present — it names the files/areas to change."""
    text = f"{ticket_markdown or ''}\n{plan_text or ''}"

    has_apex = _mentions(_APEX_RE, text)
    has_lwc = _mentions(_LWC_RE, text)
    has_flow = _mentions(_FLOW_RE, text)
    has_metadata = bool(_METADATA_RE.search(text))

    kinds = sum([has_apex, has_lwc, has_flow])
    if kinds > 1:
        return "mixed"
    if has_lwc:
        return "lwc"
    if has_apex:
        return "apex"
    if has_flow:
        return "flow"
    if has_metadata:
        components = set(re.findall(r"force-app/[\w/.\-]+", text))
        single = (
            bool(_SINGLE_HINT_RE.search(text))
            and not _PLURAL_HINT_RE.search(text)
            and len(components) <= 1
        )
        return "metadata-single" if single else "metadata"
    return "mixed"


# Per-type manager budgets. (max_turns, use_light_review)
_BUDGETS: dict[TicketType, tuple[int, bool]] = {
    "metadata-single": (8, True),
    "metadata": (12, True),
    "flow": (14, False),
    "apex": (20, False),
    "lwc": (20, False),
    "mixed": (24, False),
}


def budget_for(ticket_type: TicketType) -> tuple[int, bool]:
    """(max_turns, use_light_review). Light review = run review on the cheap
    planning model instead of the stronger review model."""
    return _BUDGETS.get(ticket_type, _BUDGETS["mixed"])
