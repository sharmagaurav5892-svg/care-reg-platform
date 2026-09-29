"""Model providers behind the LLM gateway (ADR-011).

Each provider turns the same request (a list of chat messages) into one HTTP call
and gives back the same shape (text + token counts). The gateway never needs to
know which API it is talking to.

    DatabricksChat   models hosted in the workspace (Llama 3.3 70B, gpt-oss-120b)
                     auth: the job's or notebook's own identity, no key
    GeminiChat       Google Gemini API
                     auth: API key GEMINI_API_KEY via config.secret(): .env on a laptop,
                     scope care-reg, key gemini-api-key on Databricks

Every HTTP attempt goes through ApiClient, so retries, backoff and the
ops.api_call_log audit are the same as for every other API on the platform.
Keys are only ever sent as headers, never in the URL, so they can't end up in logs.
"""
from __future__ import annotations

from dataclasses import dataclass

from careplatform.connectors.http import ApiClient


@dataclass
class Reply:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None


class ProviderUnavailable(Exception):
    """The provider can't be used right now (for example its API key isn't set up)."""


def _text_from_content(content) -> str:
    """Llama returns a string. Reasoning models (gpt-oss) return a list of blocks:
    [{"type": "reasoning", ...}, {"type": "text", "text": "..."}].
    Keep only the text blocks. The reasoning is never shown and never logged."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class DatabricksChat:
    provider = "databricks"

    def __init__(self, model: str, run_id: str, host: str | None = None, headers: dict | None = None,
                 session=None, sleep=None):
        if host is None or headers is None:
            from databricks.sdk.core import Config
            cfg = Config()          # workload identity on Databricks, your CLI login on a laptop
            host, headers = cfg.host, cfg.authenticate()
        kwargs = {"session": session}
        if sleep:
            kwargs["sleep"] = sleep
        self.model = model
        self.client = ApiClient(host, connector="databricks_fm", run_id=run_id, headers=headers, **kwargs)

    def chat(self, messages: list[dict], max_tokens: int, temperature: float) -> Reply:
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        data = self.client.post(f"/serving-endpoints/{self.model}/invocations", json=body).json()
        usage = data.get("usage") or {}
        text = _text_from_content(data["choices"][0]["message"].get("content"))
        return Reply(text.strip(), usage.get("prompt_tokens"), usage.get("completion_tokens"))


class GeminiChat:
    provider = "gemini"
    BASE = "https://generativelanguage.googleapis.com"

    def __init__(self, model: str, run_id: str, api_key: str | None, session=None, sleep=None):
        if not api_key:
            raise ProviderUnavailable(f"No API key for {model}")
        kwargs = {"session": session}
        if sleep:
            kwargs["sleep"] = sleep
        self.model = model
        self.client = ApiClient(self.BASE, connector="gemini", run_id=run_id,
                                headers={"x-goog-api-key": api_key}, **kwargs)

    def chat(self, messages: list[dict], max_tokens: int, temperature: float) -> Reply:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] != "system"]
        body = {"contents": contents,
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        data = self.client.post(f"/v1beta/models/{self.model}:generateContent", json=body).json()
        parts = data["candidates"][0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        out = (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0)
        return Reply(text.strip(), usage.get("promptTokenCount"), out or None)
