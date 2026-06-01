from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from core.models_config import Defaults, ModelsConfig, Provider, load_models_config

logger = logging.getLogger("gatekeep.llm")

# one built client per (provider, model)
_CLIENTS: dict[tuple[str, str], BaseChatModel] = {}


@lru_cache(maxsize=1)
def _config() -> ModelsConfig:
    return load_models_config()


def _build_openai(provider: Provider, model: str, defaults: Defaults) -> BaseChatModel:
    kwargs = {
        "model": model,
        "base_url": provider.base_url,
        "api_key": provider.api_key(),
        # bound stalled requests so a hung call can't block a worker
        "timeout": defaults.timeout,
        "max_retries": defaults.max_retries,
    }
    if provider.extra_body:
        kwargs["extra_body"] = provider.extra_body
    # thinking mode ignores temperature; only send it when thinking is off
    if provider.extra_body.get("thinking", {}).get("type") != "enabled":
        kwargs["temperature"] = defaults.temperature
    return ChatOpenAI(**kwargs)


# client type -> builder. Add a builder + set provider.client for a new wire protocol.
BUILDERS = {"openai": _build_openai}


def _client(provider: Provider, model: str, defaults: Defaults) -> BaseChatModel:
    cache_key = (provider.name, model)
    if cache_key not in _CLIENTS:
        builder = BUILDERS.get(provider.client)
        if builder is None:
            raise RuntimeError(
                f"provider {provider.name!r} uses unknown client {provider.client!r}; "
                f"registered: {sorted(BUILDERS)}"
            )
        _CLIENTS[cache_key] = builder(provider, model, defaults)
    return _CLIENTS[cache_key]


def llm(
    messages: list[dict],
    schema: type[BaseModel] | None = None,
    role: str = "default",
    model: str | None = None,
) -> object:
    """The ONLY place the app talks to a model. No agent imports a provider SDK.

    `role` selects the configured provider+model (see config/models.toml); `model`
    overrides it explicitly, riding on the default role's provider.
    With `schema`, returns a validated Pydantic instance; otherwise returns text.
    """
    cfg = _config()
    provider, model_name = cfg.resolve(role, model)
    client = _client(provider, model_name, cfg.defaults)
    if schema is None:
        return client.invoke(messages).content
    # include_raw surfaces the model's output on parse failure instead of dropping it
    result = client.with_structured_output(
        schema, method=provider.structured_method, include_raw=True
    ).invoke(messages)
    if result["parsing_error"]:
        logger.error(
            "structured-output parse failed for %s: %s | raw=%s",
            getattr(schema, "__name__", schema),
            result["parsing_error"],
            repr(result["raw"])[:800],
        )
        raise result["parsing_error"]
    return result["parsed"]
