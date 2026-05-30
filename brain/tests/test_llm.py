from core.schemas import Verdict
from core import llm as llm_mod


def test_structured_call_returns_schema(monkeypatch):
    class FakeStructured:
        def invoke(self, _):
            return Verdict(label="slop", confidence=0.9, reasons=["x"], primary_evidence="c")

    class FakeChat:
        def with_structured_output(self, schema):
            return FakeStructured()

    monkeypatch.setattr(llm_mod, "_client", lambda _: FakeChat())

    out = llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict)
    assert isinstance(out, Verdict)
    assert out.label == "slop"


def test_text_call_returns_content(monkeypatch):
    class FakeMsg:
        content = "hello world"

    class FakeChat:
        def invoke(self, _):
            return FakeMsg()

    monkeypatch.setattr(llm_mod, "_client", lambda _: FakeChat())

    out = llm_mod.llm([{"role": "user", "content": "hi"}])
    assert out == "hello world"
