"""CareReg: ask questions about BC seniors care law (ADR-013).

Runs in two places with the same code:

    laptop           streamlit run app/app.py            local tables, your Databricks CLI login
    Databricks App   started by the bundle (resources/app.yml)
                     sql mode through the SQL warehouse, as the app's own service principal

Every answer comes from ask() (ADR-012): exact lookup or hybrid search over live law,
the LLM gateway (ADR-011, user questions are Confidential, so Databricks-hosted models
only), and the citation check. The question text is never stored (ADR-004); the gateway
logs metrics and a hash.
"""
from __future__ import annotations

import sys
import traceback
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # the repo's code, no install needed

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from careplatform import config  # noqa: E402
from careplatform.gateway.gateway import BudgetExceeded, Gateway, GatewayError  # noqa: E402
from careplatform.gold.embed_chunks import DatabricksEmbedder  # noqa: E402
from careplatform.rag.answer import ask  # noqa: E402
from careplatform.rag.retrieve import Index  # noqa: E402

LEGAL_NOTE = ("Not legal advice. Answers come only from the loaded BC laws; "
              "the linked section of the law is the authority.")
EXAMPLES = [
    "How often must fire drills be held?",
    "Can a facility use restraints on a resident?",
    "What does section 12 of the Act say?",
]


@st.cache_resource(show_spinner="Loading the law sections...")
def load_index() -> Index:
    """Loaded once per app process: every start (and the 24-hour auto-restart) rebuilds it."""
    return Index.from_lakehouse()


def law_links() -> dict[str, str]:
    return {s["source_id"]: s["url"] for s in config.sources()["sources"]}


def loaded_on(index: Index) -> str:
    if index.units.empty:
        return "not loaded yet"
    return pd.to_datetime(index.units["valid_from"], utc=True).max().strftime("%Y-%m-%d")


def use_example(text: str) -> None:
    st.session_state.question = text


def write_audit_log(gateway: Gateway, embedder: DatabricksEmbedder) -> None:
    """Always try to write the logs; a failure is shown, never hidden, and printed to the app logs."""
    try:
        gateway.flush()
        embedder.client.flush_log()
    except Exception:
        traceback.print_exc()
        st.caption(":warning: The audit record for this answer could not be saved. The team has been told in the app logs.")


st.set_page_config(page_title="CareReg: BC care law", page_icon="⚖️", layout="centered")
st.title("CareReg: ask BC seniors care law")
st.caption("Community Care and Assisted Living Act, Residential Care Regulation, Assisted Living Regulation. "
           "Every answer cites the sections it comes from.")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

try:
    index = load_index()
except Exception:
    traceback.print_exc()
    st.error("The law sections could not be loaded. Check the app logs.")
    st.stop()

st.caption(f"{len(index)} searchable sections | law text loaded {loaded_on(index)}")

cols = st.columns(len(EXAMPLES))
for col, example in zip(cols, EXAMPLES):
    col.button(example, on_click=use_example, args=(example,), use_container_width=True)

question = st.text_input("Your question", key="question",
                         placeholder="For example: who may administer medication to a person in care?")

if st.button("Ask", type="primary") and question.strip():
    gateway = Gateway(run_id=st.session_state.session_id)
    embedder = DatabricksEmbedder(run_id=st.session_state.session_id)
    answer = None
    with st.spinner("Finding the sections and writing the answer..."):
        try:
            answer = ask(question.strip(), index, gateway, embedder)
        except BudgetExceeded:
            st.warning("Today's question limit has been reached. Please try again tomorrow.")
        except GatewayError:
            traceback.print_exc()
            st.error("No model could answer right now. Please try again in a minute.")

    if answer is not None:
        if answer.route == "status":
            st.info(answer.text)
        elif answer.route in ("not_found", "ungrounded"):
            st.warning(answer.text)
        else:
            st.markdown(answer.text)

        if answer.sources:
            st.markdown("**Sources**")
            links = law_links()
            for s in answer.sources:
                score = f" · similarity {s.score}" if s.score is not None else ""
                st.markdown(f"[{s.n}] [{s.law}]({links.get(s.source_id, '')}), {s.unit_ref} · {s.status}{score}")

        st.caption(f"route: {answer.route} · model: {answer.model or 'none'} · {answer.latency_ms} ms"
                   + (f" · call {answer.call_id[:8]}" if answer.call_id else ""))

    with st.spinner("Saving the audit record..."):
        write_audit_log(gateway, embedder)

st.divider()
st.caption(LEGAL_NOTE)
