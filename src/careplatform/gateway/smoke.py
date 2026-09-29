"""Gateway smoke test: ask every configured model one fixed question, log the calls.

    python -m careplatform.gateway.smoke

Shows, per model: answered or not, latency, tokens. Run it after changing models or
prompts, or when an endpoint is suspected down. It uses public text only, so every
allowed model (including Gemini, if its key is set up) is tried.
"""
from __future__ import annotations

import argparse

from careplatform import runtime
from careplatform.gateway.gateway import Gateway, GatewayError, chat_settings
from careplatform.runlog import start_run

QUESTION = [
    {"role": "system", "content": "Answer in one short sentence."},
    {"role": "user", "content": "What does a licensee of a residential care facility in British Columbia do?"},
]


def run() -> dict:
    out = {}
    with start_run("gateway_smoke") as r:
        for model in chat_settings()["route"]:
            gw = Gateway(run_id=r.run_id)
            gw.cfg = {**gw.cfg, "route": [model]}          # one model at a time, no fallback
            try:
                a = gw.chat("smoke", QUESTION, prompt_version="smoke-v1", data_classification="public",
                            max_tokens=200)
                out[model] = f"ok | {a.latency_ms} ms | tokens {a.prompt_tokens} in, {a.completion_tokens} out | {a.text[:120]}"
            except GatewayError as e:
                out[model] = f"not available | {e}"[:300]
            gw.flush()
        r.rows_out = sum(v.startswith("ok") for v in out.values())
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Ask every configured chat model one question.")
    runtime.add_runtime_args(p)
    runtime.apply(p.parse_args())
    print("\n=== gateway smoke test")
    for model, result in run().items():
        print(f"  {model}: {result}")


if __name__ == "__main__":
    main()
