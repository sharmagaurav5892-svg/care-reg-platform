"""The Streamlit page renders, shows its governance notes, and never calls a model on load (ADR-013).
Streamlit's own test runner executes app/app.py in-process, against an empty temporary lakehouse."""
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

APP = str(Path(__file__).resolve().parents[1] / "app" / "app.py")


def test_page_renders_with_legal_note_and_examples(lakehouse_tmp):
    at = st_testing.AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    captions = " ".join(c.value for c in at.caption)
    assert "Not legal advice" in captions
    assert "law text loaded" in captions
    labels = [b.label for b in at.button]
    assert "Ask" in labels and "What does section 12 of the Act say?" in labels


def test_example_button_fills_the_question(lakehouse_tmp):
    at = st_testing.AppTest.from_file(APP, default_timeout=30).run()
    next(b for b in at.button if b.label.startswith("How often")).click().run()
    assert at.text_input[0].value == "How often must fire drills be held?"
