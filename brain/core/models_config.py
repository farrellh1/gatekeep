from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "models.toml"


class Defaults(BaseModel):
    temperature: float = 0
    timeout: float = 120
    max_retries: int = 2


class Provider(BaseModel):
    name: str
    client: str = "openai"
    base_url: str
    api_key_env: str
    extra_body: dict = {}
    # with_structured_output method: function_calling (forces tool_choice) or json_mode
    structured_method: str = "function_calling"

    def api_key(self) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"provider {self.name!r} needs API key in ${self.api_key_env} (unset/empty)"
            )
        return key


class ModelsConfig(BaseModel):
    defaults: Defaults = Defaults()
    providers: dict[str, Provider]
    roles: dict[str, str]

    def resolve(self, role: str, model: str | None = None) -> tuple[Provider, str]:
        """Map a role (or explicit model) to its provider + model name.

        Explicit `model` wins and rides on the default role's provider.
        Otherwise the role string, then the `default` role, is consulted.
        """
        if model is not None:
            default_provider, _ = self._resolve_spec("default")
            return default_provider, model
        return self._resolve_spec(role)

    def _resolve_spec(self, role: str) -> tuple[Provider, str]:
        spec = self.roles.get(role) or self.roles.get("default")
        if not spec:
            raise RuntimeError(f"role {role!r} not in [roles] and no 'default' role defined")
        if ":" not in spec:
            raise RuntimeError(f"role {role!r} maps to {spec!r}; expected 'provider:model'")
        provider_name, _, model = spec.partition(":")
        if not provider_name or not model:
            raise RuntimeError(f"role {role!r} maps to {spec!r}; expected 'provider:model'")
        provider = self.providers.get(provider_name)
        if provider is None:
            raise RuntimeError(
                f"unknown provider {provider_name!r}; known: {sorted(self.providers)}"
            )
        return provider, model


def load_models_config(path: str | Path | None = None) -> ModelsConfig:
    """Load the TOML model config. Path resolution: arg > $GATEKEEP_MODELS_CONFIG > default."""
    resolved = Path(path or os.environ.get("GATEKEEP_MODELS_CONFIG") or _DEFAULT_PATH)
    if not resolved.is_file():
        raise RuntimeError(f"models config not found at {resolved}")
    data = tomllib.loads(resolved.read_text())
    providers = {
        name: Provider(name=name, **body) for name, body in data.get("providers", {}).items()
    }
    return ModelsConfig(
        defaults=Defaults(**data.get("defaults", {})),
        providers=providers,
        roles=data.get("roles", {}),
    )
