from __future__ import annotations

import logging
from typing import Literal

import yaml
from pydantic import BaseModel

from core.registry import REGISTRY

logger = logging.getLogger("gatekeep.config")


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
    config = RepoConfig(**data)
    _warn_unknown_checks(config)
    return config


def _warn_unknown_checks(config: RepoConfig) -> None:
    """Warn (do not raise) when a `checks` key names no registered check.

    The valid names come straight from the registry, so there is no second
    hand-maintained list to drift. A typo'd or stale key (e.g. `ci_status_check`)
    surfaces loudly but the rest of the config still runs.
    """
    valid = {c.name for c in REGISTRY}
    unknown = sorted(k for k in config.checks if k not in valid)
    if unknown:
        logger.warning(
            "Ignoring unknown check key(s) in .gatekeep.yml: %s (valid checks: %s)",
            ", ".join(unknown),
            ", ".join(sorted(valid)),
        )
