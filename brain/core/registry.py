from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core import checks
from core.schemas import Finding, NormalizedEvent

Kind = str  # "pull_request" | "issue"


@dataclass(frozen=True)
class Check:
    """A single declared unit of evidence-gathering.

    `name` is the one canonical string -- simultaneously the config key, the
    reported `Finding.check`, and the audited name -- so it cannot drift. `kinds`
    is the event kinds this Check applies to (its suite membership). `run` calls
    the check and stamps `name` onto the Finding it returns.
    """

    name: str
    kinds: tuple[Kind, ...]
    run: Callable[[NormalizedEvent], Finding]


def _adapt(
    name: str, fn: Callable[[NormalizedEvent], Finding]
) -> Callable[[NormalizedEvent], Finding]:
    """Wrap an existing check function, overwriting Finding.check with the
    registry name so the gated name and the reported name are the same string."""

    def run(event: NormalizedEvent) -> Finding:
        return fn(event).model_copy(update={"check": name})

    return run


def _check(name: str, kinds: tuple[Kind, ...], fn: Callable[[NormalizedEvent], Finding]) -> Check:
    return Check(name=name, kinds=kinds, run=_adapt(name, fn))


# The one explicit catalog. Every check is named exactly once, here.
REGISTRY: list[Check] = [
    _check("cited_symbols_exist", ("pull_request", "issue"), checks.cited_symbols_exist),
    _check("touches_real_files", ("pull_request",), checks.touches_real_files),
    _check("cosmetic_only", ("pull_request",), checks.cosmetic_only),
    _check("ci_status", ("pull_request",), checks.ci_status_check),
    _check("diff_matches_description", ("pull_request",), checks.diff_matches_description),
    _check("has_repro", ("issue",), checks.has_repro),
    _check("is_duplicate", ("issue",), checks.is_duplicate),
]


def suite_for(kind: Kind) -> list[Check]:
    """The Suite for an event kind: the registry filtered by membership."""
    return [c for c in REGISTRY if kind in c.kinds]
