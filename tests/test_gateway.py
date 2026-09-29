"""LLM gateway: routing by data classification, fallback, budget, clean answers, safe logging."""
import pytest

from careplatform import lakehouse
from careplatform.gateway import gateway as gw_mod
from careplatform.gateway.gateway import BudgetExceeded, Gateway, GatewayError, NotAllowed
from careplatform.gateway.providers import DatabricksChat, GeminiChat, ProviderUnavailable
from fakes import make_response

LLAMA, OSS, GEMINI = "databricks-meta-llama-3-3-70b-instruct", "databricks-gpt-oss-120b", "gemini-2.5-flash"
SECRET_KEY = "AIza-TEST-KEY-never-log-me"
QUESTION = "Who can administer medication to a resident?"
MSGS = [{"role": "system", "content": "Answer from the sources only."}, {"role": "user", "content": QUESTION}]


def llama_ok(text="Only trained staff.", pt=40, ct=10):
    return make_response(200, {"choices": [{"message": {"content": text}}],
                               "usage": {"prompt_tokens": pt, "completion_tokens": ct}})


def oss_ok():
    content = [{"type": "reasoning", "summary": [{"type": "summary_text", "text": f"User asks: {QUESTION}"}]},
               {"type": "text", "text": "A licensed nurse or trained staff."}]
    return make_response(200, {"choices": [{"message": {"content": content}}],
                               "usage": {"prompt_tokens": 90, "completion_tokens": 70}})


def gemini_ok():
    return make_response(200, {"candidates": [{"content": {"parts": [{"text": "Trained staff."}]}}],
                               "usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 5}})


class Script:
    """A fake HTTP session per model: returns queued responses, records requests."""
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def post(self, url, headers=None, params=None, timeout=None, json=None):
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def gateway(scripts: dict, **kw) -> Gateway:
    """Gateway whose providers talk to the fake sessions; no key for Gemini unless scripted."""
    def factory(model, spec, run_id):
        s = scripts.get(model)
        if spec["provider"] == "gemini":
            if s is None:
                raise ProviderUnavailable("No API key")
            return GeminiChat(model, run_id, api_key=SECRET_KEY, session=s, sleep=lambda x: None)
        return DatabricksChat(model, run_id, host="https://ws.example", headers={"Authorization": "Bearer t"},
                              session=s or Script(), sleep=lambda x: None)
    return Gateway(run_id="run-1", factory=factory, tokens_used_today=kw.pop("used", 0), **kw)


def test_primary_answers_and_is_logged_without_text(lakehouse_tmp):
    g = gateway({LLAMA: Script(llama_ok())})
    a = g.chat("rag", MSGS, prompt_version="answer-v1")
    assert (a.text, a.model, a.fallback_used) == ("Only trained staff.", LLAMA, False)
    assert g.flush() == 1
    log = lakehouse.read("gold.llm_call_log")
    row = log.iloc[0]
    assert (row.status, row.prompt_tokens, row.completion_tokens, row.prompt_version) == ("ok", 40, 10, "answer-v1")
    assert len(row.request_hash) == 64
    everything = log.astype(str).to_string()
    assert QUESTION not in everything and "Only trained staff" not in everything     # ADR-004


def test_falls_back_when_primary_keeps_failing(lakehouse_tmp):
    llama = Script(make_response(503), make_response(503))          # first try + 1 retry
    g = gateway({LLAMA: llama, OSS: Script(oss_ok())})
    a = g.chat("rag", MSGS, prompt_version="answer-v1")
    assert a.model == OSS and a.fallback_used
    assert a.text == "A licensed nurse or trained staff."           # reasoning block dropped
    assert len(llama.requests) == 2                                 # max_retries: 1, then move on
    g.flush()
    log = lakehouse.read("gold.llm_call_log").sort_values("called_at")
    assert list(log["status"]) == ["error", "ok"]
    assert list(log["retry_count"]) == [1, 0]
    assert QUESTION not in log.astype(str).to_string()              # not even the reasoning's copy


def test_user_questions_never_go_to_gemini(lakehouse_tmp):
    gem = Script(gemini_ok())
    g = gateway({LLAMA: Script(make_response(400), ), OSS: Script(make_response(400)), GEMINI: gem})
    with pytest.raises(GatewayError):
        g.chat("rag", MSGS, prompt_version="answer-v1", data_classification="confidential")
    assert gem.requests == []                                       # never called


def test_public_text_can_fall_back_to_gemini_with_key_in_header_only(lakehouse_tmp):
    gem = Script(gemini_ok())
    g = gateway({LLAMA: Script(make_response(400)), OSS: Script(make_response(400)), GEMINI: gem})
    a = g.chat("judge", MSGS, prompt_version="judge-v1", data_classification="public")
    assert (a.model, a.provider, a.text) == (GEMINI, "gemini", "Trained staff.")
    req = gem.requests[0]
    assert req["headers"]["x-goog-api-key"] == SECRET_KEY and SECRET_KEY not in req["url"]
    assert req["json"]["systemInstruction"]["parts"][0]["text"] == "Answer from the sources only."
    g.flush()
    assert SECRET_KEY not in lakehouse.read("ops.api_call_log").astype(str).to_string()
    assert SECRET_KEY not in lakehouse.read("gold.llm_call_log").astype(str).to_string()


def test_missing_gemini_key_is_skipped_not_fatal(lakehouse_tmp):
    g = gateway({LLAMA: Script(llama_ok())})
    g.cfg = {**g.cfg, "route": [GEMINI, LLAMA]}
    a = g.chat("judge", MSGS, prompt_version="judge-v1", data_classification="public")
    assert a.model == LLAMA and not a.fallback_used                 # Gemini was never tried


def test_restricted_data_goes_nowhere(lakehouse_tmp):
    llama = Script(llama_ok())
    g = gateway({LLAMA: llama})
    with pytest.raises(NotAllowed):
        g.chat("rag", MSGS, prompt_version="answer-v1", data_classification="restricted")
    assert llama.requests == []


def test_budget_blocks_before_sending(lakehouse_tmp, monkeypatch):
    llama = Script(llama_ok())
    g = gateway({LLAMA: llama}, used=gw_mod.chat_settings()["daily_token_budget"])
    with pytest.raises(BudgetExceeded):
        g.chat("rag", MSGS, prompt_version="answer-v1")
    assert llama.requests == []


def test_budget_counts_what_is_already_logged_today(lakehouse_tmp):
    g = gateway({LLAMA: Script(llama_ok(pt=150_000, ct=60_000))})
    g.chat("rag", MSGS, prompt_version="answer-v1")
    g.flush()
    fresh = Gateway(run_id="run-2", factory=lambda *a: None)       # reads today's usage from the log
    assert fresh.tokens_used_today() == 210_000
    with pytest.raises(BudgetExceeded):
        fresh.chat("rag", MSGS, prompt_version="answer-v1")


def test_empty_answer_counts_as_failure(lakehouse_tmp):
    g = gateway({LLAMA: Script(llama_ok(text="  ")), OSS: Script(oss_ok())})
    a = g.chat("rag", MSGS, prompt_version="answer-v1")
    assert a.model == OSS


def test_call_log_passes_its_dq_rules(lakehouse_tmp):
    g = gateway({LLAMA: Script(llama_ok())})
    g.chat("rag", MSGS, prompt_version="answer-v1")
    g.flush()
    assert g.dq.passed
    bad = lakehouse.read("gold.llm_call_log").assign(model=GEMINI, data_classification="confidential")
    from careplatform.dq.engine import run_checks
    assert not run_checks("gold.llm_call_log", bad, "x", write=False).passed      # DQ-G-011 catches it
