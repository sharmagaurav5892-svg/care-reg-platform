"""Router: does the question name a section? Then look it up exactly (ADR-012).

"What does section 12 say?" should not be answered by similarity search: the user
named the section, so we fetch that section from silver.document_units. That table keeps
every section, including repealed and not-in-force ones, so the answer can say plainly
"this section is not in force" instead of quoting it or pretending it doesn't exist.

Search never sees inactive law (it only indexes live chunks). Lookup sees everything,
and reports the status. That split is deliberate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from careplatform import config

SECTION = re.compile(r"\b(?:section|sec\.?|s\.)\s*(\d+(?:\.\d+)?(?:-\d+)?)", re.IGNORECASE)
RANGE = re.compile(r"^s\. (\d+)-(\d+)$")           # repealed blocks are stored as one unit, "s. 30-31"


def law_names() -> dict[str, list[str]]:
    """Names people use for each law, from settings.yaml rag.laws (reference data, not code)."""
    return config.settings()["rag"]["laws"]


def find_law(question: str) -> str | None:
    """The law a question names, if any. The longest matching name wins, so
    'assisted living regulation' beats a shorter, vaguer name."""
    q = question.lower()
    best, best_len = None, 0
    for source_id, names in law_names().items():
        for name in names:
            if re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", q) and len(name) > best_len:
                best, best_len = source_id, len(name)
    return best


@dataclass
class SectionRef:
    number: str             # "12"
    source_id: str | None   # a law, if the question names one


def find_section(question: str) -> SectionRef | None:
    m = SECTION.search(question)
    if not m:
        return None
    return SectionRef(m.group(1), find_law(question))


def status_of(unit: pd.Series) -> str:
    if bool(unit["is_repealed"]):
        return "repealed"
    if not bool(unit["in_force"]):
        return "not in force"
    return "in force"


def lookup(units: pd.DataFrame, ref: SectionRef) -> pd.DataFrame:
    """Current version of that section in the named law, or in every law if none is named.
    Main-body sections only: schedules restart their numbering ("Sch. A, s. 1")."""
    def matches(unit_ref: str) -> bool:
        if unit_ref == f"s. {ref.number}":
            return True
        r = RANGE.match(unit_ref)                       # "section 30" also finds "s. 30-31"
        return bool(r) and ref.number.isdigit() and int(r.group(1)) <= int(ref.number) <= int(r.group(2))

    hit = units[units["unit_ref"].map(matches)]
    if ref.source_id:
        hit = hit[hit["source_id"] == ref.source_id]
    return hit.sort_values(["source_id", "unit_order"]).reset_index(drop=True)
