import pytest

from core.schemas import Verdict
from core import llm as llm_mod


def _fake_chat(invoke_result):
    class FakeStructured:
        def invoke(self, _):
            return invoke_result

    class FakeChat:
        def with_structured_output(self, schema, method=None, include_raw=False):
            return FakeStructured()

        def invoke(self, _):
            class Msg:
                content = "hello world"
            return Msg()

    return FakeChat()


def test_structured_call_returns_parsed_schema(monkeypatch):
    verdict = Verdict(label="slop", confidence=0.9, reasons=["x"], primary_evidence="c")
    monkeypatch.setattr(
        llm_mod, "_client",
        lambda _: _fake_chat({"raw": None, "parsed": verdict, "parsing_error": None}),
    )
    out = llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict)
    assert isinstance(out, Verdict) and out.label == "slop"


def test_structured_call_raises_and_logs_on_parse_error(monkeypatch, caplog):
    err = ValueError("Invalid JSON: expected ident")
    monkeypatch.setattr(
        llm_mod, "_client",
        lambda _: _fake_chat({"raw": "firewall", "parsed": None, "parsing_error": err}),
    )
    with pytest.raises(ValueError):
        llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict)
    assert "parse failed" in caplog.text and "firewall" in caplog.text


def test_text_call_returns_content(monkeypatch):
    monkeypatch.setattr(llm_mod, "_client", lambda _: _fake_chat(None))
    out = llm_mod.llm([{"role": "user", "content": "hi"}])
    assert out == "hello world"
