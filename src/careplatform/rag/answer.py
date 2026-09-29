"""The answer pipeline: question in, cited answer (or an honest "not found") out (ADR-012).

    1. Router     names a section?  -> exact lookup, with its legal status
    2. Status     every named section is repealed / not in force -> say so, no model call
    3. Search     otherwise: hybrid search over live law only
    4. Not found  nothing close enough -> say so, no model call (saves cost, no guessing)
    5. Generate   numbered sources + prompt answer-v1 -> LLM gateway (confidential data,
                  so only Databricks-hosted models)
    6. Check      the answer must cite at least one of the numbered sources, or it is
                  replaced by the not-found message. Uncited text is never shown as an answer.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import pandas as pd

from careplatform import config
from careplatform.rag.retrieve import Index, law_titles, rag_settings
from careplatform.rag.router import find_section, lookup, status_of

NOT_FOUND_TOKEN = "NOT_FOUND"
NOT_FOUND = "I could not find this in the loaded regulations."
CITATION = re.compile(r"\[(\d+)\]")


@dataclass
class Source:
    n: int
    source_id: str
    law: str
    unit_ref: str
    status: str
    score: float | None = None


@dataclass
class RagAnswer:
    text: str
    route: str                      # lookup | status | search | not_found | ungrounded
    sources: list[Source] = field(default_factory=list)
    status_notes: list[str] = field(default_factory=list)
    model: str | None = None
    call_id: str | None = None
    latency_ms: int = 0


def load_prompt(version: str) -> str:
    return (config.config_dir() / "prompts" / f"{version}.md").read_text(encoding="utf-8")


def status_note(unit: pd.Series, titles: dict) -> str:
    law = titles.get(unit["source_id"], unit["source_id"])
    st = status_of(unit)
    heading = unit.get("heading")
    # the heading of an inactive section is often just "Not in force" or "Repealed": don't repeat it
    head = f" ({heading})" if isinstance(heading, str) and heading and heading.lower() not in (st, "repealed") else ""
    why = ("it was enacted but has not been brought into force, so it has no legal effect today"
           if st == "not in force" else "it has been repealed and no longer applies")
    note = unit.get("history_note")
    extra = f" History: {note}" if isinstance(note, str) and note else ""
    return f"{law}, {unit['unit_ref']}{head} is {st}: {why}.{extra}"


def build_messages(question: str, blocks: list[str], prompt: str) -> list[dict]:
    numbered = "\n\n".join(f"[{i}] {b}" for i, b in enumerate(blocks, start=1))
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": f"Sources:\n\n{numbered}\n\nQuestion: {question}"}]


def ask(question: str, index: Index, gateway, embedder) -> RagAnswer:
    started = time.perf_counter()
    cfg = rag_settings()
    titles = law_titles()
    ms = lambda: int((time.perf_counter() - started) * 1000)

    # 1-2. a named section: exact lookup, status first
    ref = find_section(question)
    if ref:
        units = lookup(index.units, ref)
        if len(units):
            live = units[[status_of(u) == "in force" for _, u in units.iterrows()]]
            notes = [status_note(u, titles) for _, u in units.iterrows() if status_of(u) != "in force"]
            if live.empty:
                return RagAnswer(" ".join(notes), "status", status_notes=notes, latency_ms=ms())
            sources = [Source(i, u["source_id"], titles.get(u["source_id"], u["source_id"]), u["unit_ref"],
                              "in force") for i, (_, u) in enumerate(live.iterrows(), start=1)]
            blocks = [f"[BC | {s.law} | {s.unit_ref}]\n{u['text']}" for s, (_, u) in zip(sources, live.iterrows())]
            return _generate(question, blocks, sources, notes, "lookup", gateway, cfg, ms)

    # 3-4. search live law
    hits = index.search(question, embedder.embed([question])[0])
    if not hits or max(h.vector_score for h in hits) < cfg["min_vector_score"]:
        return RagAnswer(NOT_FOUND, "not_found", latency_ms=ms())
    sources = [Source(i, h.source_id, titles.get(h.source_id, h.source_id), h.unit_ref, "in force",
                      round(h.vector_score, 3)) for i, h in enumerate(hits, start=1)]
    return _generate(question, [h.text for h in hits], sources, [], "search", gateway, cfg, ms)


def _generate(question, blocks, sources, notes, route, gateway, cfg, ms) -> RagAnswer:
    per_source = cfg["max_context_chars"] // max(1, len(blocks))    # total cap, shared by the sources
    blocks = [b[:per_source] for b in blocks]
    reply = gateway.chat("rag", build_messages(question, blocks, load_prompt(cfg["prompt_version"])),
                         prompt_version=cfg["prompt_version"], data_classification="confidential")
    text = reply.text.strip()
    cited = sorted({int(n) for n in CITATION.findall(text) if 1 <= int(n) <= len(sources)})

    if text.startswith(NOT_FOUND_TOKEN):
        text, route, cited = NOT_FOUND, "not_found", []
    elif not cited:
        text, route = NOT_FOUND, "ungrounded"          # an answer without a citation is not shown
    if notes:
        text = " ".join(notes) + "\n\n" + text
    used = [s for s in sources if s.n in cited]
    return RagAnswer(text, route, used, notes, reply.model, reply.call_id, ms())
