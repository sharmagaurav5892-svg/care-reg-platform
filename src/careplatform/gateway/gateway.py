"""The LLM gateway: the only way any code on the platform calls a chat model (ADR-011).

    from careplatform.gateway.gateway import Gateway
    gw = Gateway()
    ans = gw.chat(purpose="rag", prompt_version="answer-v1",
                  messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
                  data_classification="confidential")
    print(ans.text, ans.model, ans.fallback_used)
    gw.flush()          # write the call log

What every call gets, in this order:

  1. Data check   the data's classification decides which models may see it
                  (models.chat.models.<name>.allowed_data). Restricted data goes nowhere.
  2. Budget       tokens used today (logged + this session) must be under
                  models.chat.daily_token_budget, or nothing is sent.
  3. Route        models tried in models.chat.route order. A failure (after a short
                  retry) moves on to the next model: that's the fallback.
  4. Clean up     only the answer text comes back; reasoning blocks are dropped.
  5. Log          one row per model attempt in gold.llm_call_log: purpose, model,
                  prompt version, tokens, latency, status, fallback, cost, and a
                  SHA-256 of the prompt. Never the prompt, the answer or a key (ADR-004).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import pandas as pd

from careplatform import config, lakehouse
from careplatform.dq.engine import run_checks
from careplatform.gateway.providers import DatabricksChat, GeminiChat, ProviderUnavailable

LOG_TABLE = "gold.llm_call_log"
LEVELS = ["public", "internal", "confidential", "restricted"]    # docs/02


class GatewayError(Exception):
    """No model could answer."""


class NotAllowed(GatewayError):
    """The data may not be sent to any configured model."""


class BudgetExceeded(GatewayError):
    """Today's token budget is used up. Nothing was sent."""


@dataclass
class Answer:
    text: str
    model: str
    provider: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int
    fallback_used: bool
    call_id: str


def chat_settings() -> dict:
    return config.settings()["models"]["chat"]


def default_factory(model: str, spec: dict, run_id: str):
    """Build the provider object for one configured model."""
    if spec["provider"] == "databricks":
        return DatabricksChat(model, run_id)
    if spec["provider"] == "gemini":
        return GeminiChat(model, run_id, api_key=config.secret(spec["api_key_secret"]))
    raise ValueError(f"Unknown provider {spec['provider']!r} for {model}")


def cost_usd(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float:
    price = (config.settings().get("pricing") or {}).get(model) or {}
    return round((prompt_tokens or 0) / 1e6 * price.get("input_per_1m", 0.0)
                 + (completion_tokens or 0) / 1e6 * price.get("output_per_1m", 0.0), 8)


class Gateway:
    def __init__(self, run_id: str | None = None,
                 factory: Callable[[str, dict, str], object] = default_factory,
                 tokens_used_today: int | None = None):
        self.cfg = chat_settings()
        self.run_id = run_id or str(uuid.uuid4())
        self.factory = factory
        self.rows: list[dict] = []
        self._providers: dict[str, object] = {}
        self._used_before = tokens_used_today      # None = read from the log on first call
        self.dq = None

    # ---------- the one public call ----------

    def chat(self, purpose: str, messages: list[dict], prompt_version: str,
             data_classification: str = "confidential", max_tokens: int | None = None) -> Answer:
        if data_classification not in LEVELS:
            raise NotAllowed(f"Unknown data classification {data_classification!r}")
        route = [m for m in self.cfg["route"]
                 if data_classification in self.cfg["models"][m]["allowed_data"]]
        if not route:
            raise NotAllowed(f"No model is allowed to receive {data_classification} data")

        budget = self.cfg["daily_token_budget"]
        if self.tokens_used_today() >= budget:
            raise BudgetExceeded(f"Daily token budget of {budget:,} is used up. Nothing was sent.")

        request_hash = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        max_tokens = max_tokens or self.cfg["max_tokens"]
        problems, first_tried = [], None

        for model in route:
            try:
                p = self._provider(model)
            except ProviderUnavailable as e:
                problems.append(f"{model}: {e}")
                continue
            first_tried = first_tried or model
            before = len(p.client.calls)
            started = time.perf_counter()
            error, reply = None, None
            try:
                reply = p.chat(messages, max_tokens=max_tokens, temperature=self.cfg["temperature"])
                if not reply.text:
                    error = "EmptyAnswer"
            except Exception as e:                       # HTTP error, bad JSON, timeout...
                error = type(e).__name__
            latency = int((time.perf_counter() - started) * 1000)
            row = self._log(purpose, p, model, prompt_version, data_classification, reply, error,
                            latency, retries=max(0, len(p.client.calls) - before - 1),
                            fallback=model != first_tried, request_hash=request_hash)
            if error is None:
                return Answer(reply.text, model, p.provider, reply.prompt_tokens, reply.completion_tokens,
                              latency, row["fallback_used"], row["call_id"])
            problems.append(f"{model}: {error}")

        raise GatewayError("No model answered. " + "; ".join(problems))

    # ---------- helpers ----------

    def _provider(self, model: str):
        if model not in self._providers:
            p = self.factory(model, self.cfg["models"][model], self.run_id)
            p.client.max_retries = self.cfg["max_retries"]     # fail over fast instead of waiting minutes
            p.client.timeout = self.cfg["timeout_seconds"]
            self._providers[model] = p
        return self._providers[model]

    def tokens_used_today(self) -> int:
        if self._used_before is None:
            log = lakehouse.read(LOG_TABLE)
            today = datetime.now(timezone.utc).date()
            if len(log):
                log = log[pd.to_datetime(log["called_at"], utc=True).dt.date == today]
            self._used_before = int(log["prompt_tokens"].fillna(0).sum()
                                    + log["completion_tokens"].fillna(0).sum()) if len(log) else 0
        mine = sum((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0) for r in self.rows)
        return self._used_before + mine

    def _log(self, purpose, p, model, prompt_version, classification, reply, error, latency,
             retries, fallback, request_hash) -> dict:
        pt = reply.prompt_tokens if reply else None
        ct = reply.completion_tokens if reply else None
        row = {
            "call_id": str(uuid.uuid4()),
            "called_at": datetime.now(timezone.utc),
            "run_id": self.run_id,
            "purpose": purpose,
            "provider": p.provider,
            "model": model,
            "prompt_version": prompt_version,
            "data_classification": classification,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "latency_ms": latency,
            "status": "ok" if error is None else "error",
            "error_type": error,
            "retry_count": retries,
            "fallback_used": fallback,
            "cost_usd": cost_usd(model, pt, ct),
            "request_hash": request_hash,
        }
        self.rows.append(row)
        return row

    def flush(self) -> int:
        """Write the model call log and the HTTP attempt log. Call once at the end of a run or session.

        The log is checked against its DQ rules (G-006 cost, G-011 data classification) and the
        results go to ops.dq_results. It is still written if a rule fails: the calls already
        happened, and the log is the evidence. The failure shows up in DQ monitoring."""
        n = 0
        if self.rows:
            df = pd.DataFrame(self.rows)
            self.dq = run_checks(LOG_TABLE, df, self.run_id)
            n = lakehouse.append(LOG_TABLE, df)
        for p in self._providers.values():
            p.client.flush_log()
        self._used_before = self.tokens_used_today()
        self.rows = []
        return n
