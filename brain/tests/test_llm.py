import pytest

from core import llm as llm_mod
from core import models_config as mc
from core.schemas import Verdict


def _config():
    return mc.ModelsConfig(
        defaults=mc.Defaults(temperature=0, timeout=90, max_retries=3),
        providers={
            "openrouter": mc.Provider(
                name="openrouter",
                client="openai",
                base_url="https://openrouter.ai/api/v1",
                api_key_env="OPENROUTER_API_KEY",
            ),
            "deepseek": mc.Provider(
                name="deepseek",
                client="openai",
                base_url="https://api.deepseek.com",
                api_key_env="DEEPSEEK_API_KEY",
                extra_body={"thinking": {"type": "disabled"}},
            ),
        },
        roles={
            "default": "openrouter:deepseek/deepseek-v4",
            "judge": "openrouter:anthropic/claude-opus-4-8",
            "responder": "deepseek:deepseek-v4",
        },
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_mod, "_config", _config)
    llm_mod._CLIENTS.clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")


def _install_fake_chat(monkeypatch, structured=None, captured=None):
    class FakeStructured:
        def invoke(self, _):
            return structured

    class FakeChat:
        def __init__(self, **kw):
            if captured is not None:
                captured.update(kw)

        def with_structured_output(self, schema, method=None, include_raw=False):
            if captured is not None:
                captured["method"] = method
            return FakeStructured()

        def invoke(self, _):
            class Msg:
                content = "hello world"

            return Msg()

    monkeypatch.setattr(llm_mod, "ChatOpenAI", FakeChat)
    return captured


def test_judge_role_builds_client_with_provider_settings(monkeypatch):
    captured = _install_fake_chat(monkeypatch, captured={})
    llm_mod.llm([{"role": "user", "content": "hi"}], role="judge")
    assert captured["model"] == "anthropic/claude-opus-4-8"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["api_key"] == "sk-or"
    assert captured["temperature"] == 0
    assert captured["timeout"] == 90
    assert captured["max_retries"] == 3
    assert "extra_body" not in captured  # openrouter has none


def test_thinking_provider_omits_temperature(monkeypatch):
    # thinking mode ignores temperature; don't send a value the API discards
    def _cfg():
        c = _config()
        c.providers["openrouter"].extra_body = {"thinking": {"type": "enabled"}}
        return c

    monkeypatch.setattr(llm_mod, "_config", _cfg)
    captured = _install_fake_chat(monkeypatch, captured={})
    llm_mod.llm([{"role": "user", "content": "hi"}], role="judge")
    assert "temperature" not in captured
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}


def test_provider_extra_body_passed_through(monkeypatch):
    captured = _install_fake_chat(monkeypatch, captured={})
    llm_mod.llm([{"role": "user", "content": "hi"}], role="responder")
    assert captured["model"] == "deepseek-v4"
    assert captured["api_key"] == "sk-ds"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_structured_method_defaults_to_function_calling(monkeypatch):
    verdict = Verdict(label="legit", confidence=0.5, reasons=["x"], primary_evidence="c")
    captured = _install_fake_chat(
        monkeypatch,
        structured={"raw": None, "parsed": verdict, "parsing_error": None},
        captured={},
    )
    llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict, role="judge")
    assert captured["method"] == "function_calling"


def test_provider_structured_method_override(monkeypatch):
    def _cfg():
        c = _config()
        c.providers["openrouter"].structured_method = "json_mode"
        return c

    monkeypatch.setattr(llm_mod, "_config", _cfg)
    verdict = Verdict(label="legit", confidence=0.5, reasons=["x"], primary_evidence="c")
    captured = _install_fake_chat(
        monkeypatch,
        structured={"raw": None, "parsed": verdict, "parsing_error": None},
        captured={},
    )
    llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict, role="judge")
    assert captured["method"] == "json_mode"


def test_unknown_client_type_raises(monkeypatch):
    def _bad_config():
        cfg = _config()
        cfg.providers["openrouter"].client = "anthropic"
        return cfg

    monkeypatch.setattr(llm_mod, "_config", _bad_config)
    _install_fake_chat(monkeypatch)
    with pytest.raises(RuntimeError, match="anthropic"):
        llm_mod.llm([{"role": "user", "content": "hi"}], role="judge")


def test_structured_call_returns_parsed_schema(monkeypatch):
    verdict = Verdict(label="slop", confidence=0.9, reasons=["x"], primary_evidence="c")
    _install_fake_chat(
        monkeypatch, structured={"raw": None, "parsed": verdict, "parsing_error": None}
    )
    out = llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict)
    assert isinstance(out, Verdict) and out.label == "slop"


def test_structured_call_raises_and_logs_on_parse_error(monkeypatch, caplog):
    err = ValueError("Invalid JSON: expected ident")
    _install_fake_chat(
        monkeypatch, structured={"raw": "firewall", "parsed": None, "parsing_error": err}
    )
    with pytest.raises(ValueError):
        llm_mod.llm([{"role": "user", "content": "hi"}], schema=Verdict)
    assert "parse failed" in caplog.text and "firewall" in caplog.text


def test_text_call_returns_content(monkeypatch):
    _install_fake_chat(monkeypatch)
    out = llm_mod.llm([{"role": "user", "content": "hi"}])
    assert out == "hello world"
