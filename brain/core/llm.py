from __future__ import annotations

import os
from typing import Optional, Type

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

DEFAULT_MODEL = os.environ.get("GATEKEEP_MODEL", "deepseek/deepseek-v4-pro")


def _client(model: str) -> ChatOpenAI:
    """OpenRouter is OpenAI-compatible; point the client at its base URL.

    BYO-key: the caller's OPENROUTER_API_KEY. Model is just a string, so the
    whole app is model-agnostic — swap models via config, no code change.
    """
    return ChatOpenAI(
        model=model,
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        temperature=0,
    )


def llm(
    messages: list[dict],
    schema: Optional[Type[BaseModel]] = None,
    model: Optional[str] = None,
) -> object:
    """The ONLY place the app talks to a model. No agent imports a provider SDK.

    With `schema`, returns a validated Pydantic instance; otherwise returns text.
    `model` overrides the default per-agent (e.g. a stronger model for Judge).
    """
    client = _client(model or DEFAULT_MODEL)
    if schema is not None:
        return client.with_structured_output(schema).invoke(messages)
    return client.invoke(messages).content
