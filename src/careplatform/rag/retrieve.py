"""Retrieval: find the law sections that match a question (ADR-012).

Two ways of matching, combined:

    vector   the question and every chunk are turned into vectors by the same model
             (databricks-gte-large-en); close vectors = similar meaning.
             Good at "who may give medicine" -> "administration of medication".
    keyword  BM25, the classic search-engine score: rare words that appear in a chunk
             count more than common ones. Good at exact terms like "fire drill" or "restraint".

The two ranked lists are merged with Reciprocal Rank Fusion (RRF): a chunk's score is
the sum of 1 / (rrf_k + its rank) in each list, so a chunk ranked high in either list rises.

Only searchable text is loaded: active chunks, not PII-flagged, with an active vector for
the configured model. Repealed, amended and not-in-force text never reached those tables,
so it can't be found here (DQ-S-008, DQ-G-010). The full unit table is kept separately
for exact section lookups, where status matters (router.py).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np
import pandas as pd

from careplatform import config, lakehouse

WORD = re.compile(r"[a-z0-9]+")
STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how", "i",
        "if", "in", "is", "it", "must", "of", "on", "or", "the", "to", "what", "when", "which", "who",
        "with", "under", "there", "any", "my", "we", "you", "should", "shall", "may", "this", "that"}


def rag_settings() -> dict:
    return config.settings()["rag"]


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


def law_titles() -> dict[str, str]:
    return {s["source_id"]: s["title"] for s in config.sources()["sources"]}


@dataclass
class Hit:
    chunk_id: str
    source_id: str
    unit_ref: str
    text: str
    vector_score: float
    keyword_score: float
    score: float


class Index:
    """Everything the app may search, held in memory (281 chunks is small)."""

    def __init__(self, chunks: pd.DataFrame, vectors: pd.DataFrame, units: pd.DataFrame):
        model = config.settings()["models"]["embeddings"]["endpoint"]
        live = chunks[chunks["is_active"].astype(bool) & ~chunks["pii_flag"].astype(bool)]
        vec = vectors[vectors["is_active"].astype(bool) & (vectors["embedding_model"] == model)]
        rows = live.merge(vec[["chunk_id", "vector"]], on="chunk_id", how="inner")
        self.rows = rows.reset_index(drop=True)
        m = np.array([np.asarray(v, dtype=float) for v in self.rows["vector"]]) if len(self.rows) \
            else np.zeros((0, 1))
        norms = np.linalg.norm(m, axis=1, keepdims=True) if len(m) else 1
        self.matrix = m / np.where(norms == 0, 1, norms)
        self.units = units[units["is_current"].astype(bool)].reset_index(drop=True)

        # BM25 statistics over the chunk text (header included, so law and section names match)
        self.docs = [Counter(words(t)) for t in self.rows["text"]]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df = Counter(w for d in self.docs for w in d)
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    @classmethod
    def from_lakehouse(cls) -> "Index":
        return cls(lakehouse.read("silver.chunks"), lakehouse.read("gold.chunk_embeddings"),
                   lakehouse.read("silver.document_units"))

    def __len__(self) -> int:
        return len(self.rows)

    def keyword_scores(self, question: str, k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        q = set(words(question))
        out = np.zeros(len(self.docs))
        for i, (d, length) in enumerate(zip(self.docs, self.lengths)):
            s = 0.0
            for w in q:
                f = d.get(w, 0)
                if f:
                    s += self.idf[w] * f * (k1 + 1) / (f + k1 * (1 - b + b * length / self.avg_len))
            out[i] = s
        return out

    def search(self, question: str, question_vector: list[float]) -> list[Hit]:
        cfg = rag_settings()
        if not len(self.rows):
            return []
        q = np.asarray(question_vector, dtype=float)
        q = q / (np.linalg.norm(q) or 1)
        vec = self.matrix @ q
        kw = self.keyword_scores(question)

        by_vec = np.argsort(-vec)[:cfg["candidates"]]
        by_kw = [i for i in np.argsort(-kw)[:cfg["candidates"]] if kw[i] > 0]
        fused: dict[int, float] = {}
        for ranking in (by_vec, by_kw):
            for rank, i in enumerate(ranking, start=1):
                fused[int(i)] = fused.get(int(i), 0.0) + 1.0 / (cfg["rrf_k"] + rank)

        best = sorted(fused, key=lambda i: -fused[i])[:cfg["top_k"]]
        return [Hit(self.rows.at[i, "chunk_id"], self.rows.at[i, "source_id"], self.rows.at[i, "unit_ref"],
                    self.rows.at[i, "text"], float(vec[i]), float(kw[i]), fused[i]) for i in best]
