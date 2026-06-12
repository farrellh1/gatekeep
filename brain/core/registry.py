from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core import checks
from core.run_context import RunContext
from core.schemas import CheckResult, Finding, NormalizedEvent

Kind = str  # "pull_request" | "issue"

CheckFn = Callable[[NormalizedEvent, RunContext], CheckResult]


@dataclass(frozen=True)
class Check:
    """A single declared unit of evidence-gathering.

    `name` is the one canonical string -- simultaneously the config key, the
    reported `Finding.check`, and the audited name -- so it cannot drift. `kinds`
    is the event kinds this Check applies to (its suite membership). `run` calls
    the nameless check and composes `name` onto the `CheckResult` it returns.
    """

    name: str
    kinds: tuple[Kind, ...]
    run: Callable[[NormalizedEvent, RunContext], Finding]


def _adapt(name: str, fn: CheckFn) -> Callable[[NormalizedEvent, RunContext], Finding]:
    """Wrap a nameless check, composing the registry name onto its CheckResult.
    The check cannot name itself, so the gated and reported name are one string."""

    def run(event: NormalizedEvent, ctx: RunContext) -> Finding:
        return Finding(check=name, **fn(event, ctx).model_dump())

    return run


def _check(name: str, kinds: tuple[Kind, ...], fn: CheckFn) -> Check:
    return Check(name=name, kinds=kinds, run=_adapt(name, fn))


# The one explicit catalog. Every check is named exactly once, here.
REGISTRY: list[Check] = [
    _check("cited_symbols_exist", ("pull_request", "issue"), checks.cited_symbols_exist),
    _check("cosmetic_only", ("pull_request",), checks.cosmetic_only),
    _check("ci_status", ("pull_request",), checks.ci_status_check),
    _check("diff_matches_description", ("pull_request",), checks.diff_matches_description),
    _check("substantive_description", ("pull_request",), checks.substantive_description),
    _check("fix_addresses_issue", ("pull_request",), checks.fix_addresses_issue),
    _check("has_repro", ("issue",), checks.has_repro),
    _check("is_duplicate", ("issue",), checks.is_duplicate),
    _check("template_untouched", ("pull_request",), checks.template_untouched),
    _check(
        "verification_claims_unsupported", ("pull_request",), checks.verification_claims_unsupported
    ),
    _check("claimed_bug_exists", ("pull_request",), checks.claimed_bug_exists),
    _check("first_contribution", ("pull_request",), checks.first_contribution),
]


def suite_for(kind: Kind) -> list[Check]:
    """The Suite for an event kind: the registry filtered by membership."""
    return [c for c in REGISTRY if kind in c.kinds]
