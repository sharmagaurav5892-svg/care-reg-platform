"""Parse a BC Laws XML document into units (sections) and cross references.

Pure function: bytes in, rows out. No lakehouse, no Spark, so it's easy to test.
The shape it handles comes from profiling the real files (ADR-009):

    act|regulation
      content
        part (num, text)             -> context_path "Part 4 Care and Supervision"
          division (num, text)       -> "... > Division 2 Staffing"
            section                  -> ONE UNIT
              marginalnote           -> heading
              num                    -> unit_ref "s. 12" (can be a range, "30-31")
              text                   -> body
              subsection|paragraph|subparagraph|clause|subclause (num, text, ...)
                                     -> "(1) ...", "(a) ...", nested
              definition, table      -> rendered in place
              hnote                  -> history_note
        schedule                     -> sections inside get "Sch. 2, s. 1" (numbering restarts)
        conseqhead                   -> consequential amendments bookkeeping: skipped

"Not in force" (enacted but never brought into effect, e.g. CCALA s. 12) is handled
the same way as repeal: a whole section gets in_force = False and is never chunked;
a single subsection marked "[Not in force.]" is dropped from the text with a note.

Repeal is handled at two levels (the profile showed both):
  * whole section: heading is "Repealed"  -> is_repealed = True, kept, never chunked
  * one subsection or lower: its text starts "Repealed" -> that piece is dropped from
    the text, its note goes to history_note, the rest of the section stays
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NUMBERED = {"subsection", "paragraph", "subparagraph", "clause", "subclause"}
CONTAINERS = {"part", "division"}
SKIP = {"conseqhead"}                      # consequential amendments table, not rules
REPEALED = re.compile(r"^\[?\s*repealed\b", re.IGNORECASE)
NOT_IN_FORCE = re.compile(r"^\[?\s*not in force\b", re.IGNORECASE)   # enacted but never brought into effect
LEG_HREF = re.compile(r"/legislation/([^/?#]+)")


def local(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def text_of(el: ET.Element) -> str:
    return clean("".join(el.itertext()))     # inline tags (term, link) join without extra spaces


def child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if local(c) == name), None)


def child_text(el: ET.Element, name: str) -> str:
    c = child(el, name)
    return text_of(c) if c is not None else ""


@dataclass
class Unit:
    unit_ref: str
    unit_order: int
    context_path: str | None
    heading: str | None
    text: str
    history_note: str | None
    is_repealed: bool
    in_force: bool
    links: list[tuple[str, str]] = field(default_factory=list)    # (href, link text)


def _render_table(table: ET.Element) -> list[str]:
    """One line per row, with the column names repeated, so a row can't be misread."""
    rows = [[text_of(e) for e in r if local(e) == "entry"] for r in table.iter() if local(r) == "trow"]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if not body:
        return [" | ".join(header)]
    lines = []
    for r in body:
        cells = [f"{h}: {v}" if h else v for h, v in zip(header, r)]
        lines.append(" | ".join(c for c in cells if c))
    return lines


def _render(el: ET.Element, notes: list[str]) -> list[str]:
    """Body lines of a section or a numbered level, depth first, in document order."""
    tag = local(el)
    num = child_text(el, "num")
    own_text = child_text(el, "text")
    if tag in NUMBERED and (REPEALED.match(own_text) or NOT_IN_FORCE.match(own_text)):
        notes.append(f"({num}) {own_text}" if num else own_text)
        return []

    prefix = f"({num}) " if tag in NUMBERED and num else ""
    lines: list[str] = []
    for c in el:
        t = local(c)
        if t in ("num", "marginalnote"):
            continue
        if t == "hnote":
            notes.append(text_of(c))
        elif t == "text":
            lines.append(prefix + text_of(c))
            prefix = ""
        elif t == "table":
            lines += _render_table(c)
        elif t in SKIP:
            continue
        elif len(c):
            lines += _render(c, notes)
        else:
            s = text_of(c)
            if s:
                lines.append(prefix + s)
                prefix = ""
    if prefix and not lines:          # a numbered level with children but no own text
        lines.append(prefix.strip())
    return [ln for ln in lines if ln]


def _label(el: ET.Element, kind: str) -> str:
    """'Part 4 Care and Supervision', 'Division 2 Staffing'."""
    num = child_text(el, "num")
    title = child_text(el, "text") or child_text(el, "title")
    return clean(f"{kind} {num} {title}")


def _schedule_label(el: ET.Element, ordinal: int) -> str:
    for name in ("scheduletitle", "title", "num"):
        s = child_text(el, name)
        if s:
            s = re.sub(r"^schedule\s*", "", s, flags=re.IGNORECASE)
            return f"Sch. {s}"
    return f"Sch. {ordinal}"


def parse(xml_bytes: bytes) -> list[Unit]:
    root = ET.fromstring(xml_bytes)
    units: list[Unit] = []
    schedules = 0

    def walk(el: ET.Element, path: list[str], sched: str | None) -> None:
        nonlocal schedules
        for c in el:
            t = local(c)
            if t in SKIP:
                continue
            if t in CONTAINERS:
                walk(c, path + [_label(c, t.capitalize())], sched)
            elif t == "schedule":
                schedules += 1
                label = _schedule_label(c, schedules)
                walk(c, [label], label)
            elif t == "section":
                units.append(_section(c, path, sched, len(units) + 1))
            elif len(c):
                walk(c, path, sched)

    walk(root, [], None)
    return units


def _section(sec: ET.Element, path: list[str], sched: str | None, order: int) -> Unit:
    num = child_text(sec, "num") or str(order)
    heading = child_text(sec, "marginalnote") or None
    notes: list[str] = []
    lines = _render(sec, notes)
    text = "\n".join(lines)
    whole_repeal = (heading or "").strip().lower() == "repealed" or (not lines and bool(notes)) \
        or bool(REPEALED.match(text))
    in_force = (heading or "").strip().lower() != "not in force" and not NOT_IN_FORCE.match(text)
    links = []
    for a in sec.iter():
        if local(a) == "link":
            href = next((v for k, v in a.attrib.items() if k.split("}")[-1] == "href"), None)
            if href:
                links.append((href, text_of(a)))
    return Unit(
        unit_ref=f"{sched}, s. {num}" if sched else f"s. {num}",
        unit_order=order,
        context_path=" > ".join(path) or None,
        heading=heading,
        text=text,
        history_note="; ".join(n for n in notes if n) or None,
        is_repealed=whole_repeal,
        in_force=in_force,
        links=links,
    )


def target_doc_id(href: str) -> str | None:
    m = LEG_HREF.search(href)
    return m.group(1) if m else None
