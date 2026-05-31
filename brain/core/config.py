from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel


class RepoConfig(BaseModel):
    mode: Literal["suggest-only", "auto-gate"] = "suggest-only"
    threshold: float = 0.85
    labels: dict[str, str] = {"slop": "possible-slop", "needs_info": "needs-repro"}
    checks: dict[str, bool] = {}  # check_name -> enabled; absent = enabled

    def is_check_enabled(self, name: str) -> bool:
        return self.checks.get(name, True)


def load_config(yaml_text: str | None) -> RepoConfig:
    """Parse a .gatekeep.yml string. None/empty -> defaults (read-only)."""
    if not yaml_text:
        return RepoConfig()
    data = yaml.safe_load(yaml_text) or {}
    return RepoConfig(**data)
