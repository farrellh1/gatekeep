import textwrap

import pytest

from core import models_config as mc

_TOML = textwrap.dedent(
    """
    [defaults]
    temperature = 0
    timeout     = 90
    max_retries = 3

    [providers.openrouter]
    client      = "openai"
    base_url    = "https://openrouter.ai/api/v1"
    api_key_env = "OPENROUTER_API_KEY"

    [providers.deepseek]
    client      = "openai"
    base_url    = "https://api.deepseek.com"
    api_key_env = "DEEPSEEK_API_KEY"
    extra_body  = { thinking = { type = "disabled" } }

    [roles]
    default   = "openrouter:deepseek/deepseek-v4"
    judge     = "openrouter:anthropic/claude-opus-4-8"
    responder = "deepseek:deepseek-v4"
    """
)


def _write(tmp_path, text=_TOML):
    p = tmp_path / "models.toml"
    p.write_text(text)
    return p


def test_parses_defaults_providers_roles(tmp_path):
    cfg = mc.load_models_config(_write(tmp_path))
    assert cfg.defaults.timeout == 90
    assert cfg.defaults.max_retries == 3
    assert set(cfg.providers) == {"openrouter", "deepseek"}
    assert cfg.providers["deepseek"].extra_body == {"thinking": {"type": "disabled"}}
    assert cfg.roles["judge"] == "openrouter:anthropic/claude-opus-4-8"


def test_resolve_role_returns_provider_and_model(tmp_path):
    cfg = mc.load_models_config(_write(tmp_path))
    provider, model = cfg.resolve("judge")
    assert provider.name == "openrouter"
    assert model == "anthropic/claude-opus-4-8"


def test_resolve_unknown_role_falls_back_to_default(tmp_path):
    cfg = mc.load_models_config(_write(tmp_path))
    provider, model = cfg.resolve("nonexistent")
    assert provider.name == "openrouter"
    assert model == "deepseek/deepseek-v4"


def test_resolve_explicit_model_uses_default_provider(tmp_path):
    cfg = mc.load_models_config(_write(tmp_path))
    provider, model = cfg.resolve("judge", model="some/other-model")
    assert provider.name == "openrouter"  # default role's provider
    assert model == "some/other-model"


def test_api_key_read_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    cfg = mc.load_models_config(_write(tmp_path))
    provider, _ = cfg.resolve("judge")
    assert provider.api_key() == "sk-test-123"


def test_missing_key_raises_naming_provider_and_var(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = mc.load_models_config(_write(tmp_path))
    provider, _ = cfg.resolve("judge")
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        provider.api_key()


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        mc.load_models_config(tmp_path / "nope.toml")


def test_malformed_role_string_raises(tmp_path):
    bad = '[providers.x]\nbase_url="u"\napi_key_env="K"\n[roles]\ndefault="no-colon"\n'
    cfg = mc.load_models_config(_write(tmp_path, bad))
    with pytest.raises(RuntimeError, match="provider:model"):
        cfg.resolve("default")


def test_unknown_provider_in_role_raises(tmp_path):
    bad = '[providers.x]\nbase_url="u"\napi_key_env="K"\n[roles]\ndefault="ghost:m"\n'
    cfg = mc.load_models_config(_write(tmp_path, bad))
    with pytest.raises(RuntimeError, match="unknown provider"):
        cfg.resolve("default")


def test_no_default_role_raises(tmp_path):
    bad = '[providers.x]\nbase_url="u"\napi_key_env="K"\n[roles]\njudge="x:m"\n'
    cfg = mc.load_models_config(_write(tmp_path, bad))
    with pytest.raises(RuntimeError, match="no 'default' role"):
        cfg.resolve("missing")


def test_shipped_config_parses_and_every_role_resolves():
    cfg = mc.load_models_config()  # default path: brain/config/models.toml
    assert "default" in cfg.roles
    for role in cfg.roles:
        provider, model = cfg.resolve(role)
        assert provider.client in {"openai"}
        assert model
