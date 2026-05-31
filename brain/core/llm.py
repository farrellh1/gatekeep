from __future__ import annotations

import logging
import os

from langchain_openai import ChatOpenAI
from pydantic import BaseModel

DEFAULT_MODEL = os.environ.get("GATEKEEP_MODEL", "deepseek/deepseek-v4-pro")

logger = logging.getLogger("gatekeep.llm")


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
    schema: type[BaseModel] | None = None,
    model: str | None = None,
) -> object:
    """The ONLY place the app talks to a model. No agent imports a provider SDK.

    With `schema`, returns a validated Pydantic instance; otherwise returns text.
    `model` overrides the default per-agent (e.g. a stronger model for Judge).
    """
    client = _client(model or DEFAULT_MODEL)
    if schema is None:
        return client.invoke(messages).content
    # function_calling yields stricter JSON than json-schema mode on some OpenRouter
    # providers; include_raw surfaces the model's actual output when parsing fails
    # instead of discarding it.
    result = client.with_structured_output(
        schema, method="function_calling", include_raw=True
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
