"""Ask a question from the terminal.

    python -m careplatform.rag.ask "How often must fire drills be held?"

Loads the searchable law from the lakehouse, answers through the LLM gateway, prints the
answer with its sources, and writes the gateway's call log. The question itself is never
stored (ADR-004); only the gateway's metrics row and a hash of the request are.
"""
from __future__ import annotations

import argparse
import uuid

from careplatform import runtime
from careplatform.gateway.gateway import Gateway, GatewayError
from careplatform.gold.embed_chunks import DatabricksEmbedder
from careplatform.rag.answer import ask
from careplatform.rag.retrieve import Index

LEGAL_NOTE = "Not legal advice. The linked section of the law is the authority."


def main() -> None:
    p = argparse.ArgumentParser(description="Ask a question about the loaded BC care legislation.")
    p.add_argument("question")
    runtime.add_runtime_args(p)
    a = p.parse_args()
    runtime.apply(a)

    session = str(uuid.uuid4())
    index = Index.from_lakehouse()
    gateway = Gateway(run_id=session)
    embedder = DatabricksEmbedder(run_id=session)
    try:
        ans = ask(a.question, index, gateway, embedder)
    except GatewayError as e:
        print(f"\nNo answer: {e}")
        return
    finally:
        gateway.flush()
        embedder.client.flush_log()

    print(f"\n{ans.text}\n")
    for s in ans.sources:
        score = f" (similarity {s.score})" if s.score is not None else ""
        print(f"  [{s.n}] {s.law}, {s.unit_ref}{score}")
    print(f"\n  route: {ans.route} | model: {ans.model or 'none'} | {ans.latency_ms} ms | searchable chunks: {len(index)}")
    print(f"  {LEGAL_NOTE}")


if __name__ == "__main__":
    main()
