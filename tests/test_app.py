"""Smoke-test the Streamlit app itself (offline LLM, no network)."""

import os

import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "APP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def _app():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_home_renders_examples():
    at = _app()
    labels = [b.label for b in at.button]
    assert any("budget vs actual" in label.lower() for label in labels)


def test_example_question_answers_with_sql_and_chart():
    at = _app()
    next(b for b in at.button if b.key and b.key.startswith("example-")).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("SELECT" in c.value for c in at.code)
    assert len(at.chat_message) == 2


def test_unknown_question_offline_is_handled():
    at = _app()
    at.chat_input[0].set_value("What's the weather on Mars?").run()
    assert not at.exception
    assert any("offline" in i.value.lower() for i in at.info)


def _render_view(view: str, root: str):
    import importlib
    import sys

    sys.path.insert(0, root)

    from ui.styles import inject_css

    inject_css()
    importlib.import_module(f"ui.views.{view}").render()


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.parametrize("view", ["catalog", "upload", "about"])
def test_other_pages_render(view):
    at = AppTest.from_function(_render_view, args=(view, ROOT), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.markdown


def test_password_gate(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert at.text_input and not any(b.key and str(b.key).startswith("example-") for b in at.button)
    at.text_input[0].input("demo")
    at.text_input[1].input("wrong")
    at.button[0].click().run()
    assert at.error
    at.text_input[1].input("s3cret")
    at.button[0].click().run()
    assert any(b.key and str(b.key).startswith("example-") for b in at.button)
