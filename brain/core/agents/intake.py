from __future__ import annotations

from pydantic import BaseModel

from core.llm import llm
from core.schemas import BrainState, IntakeResult

_ACTIONABLE = {"opened", "edited", "reopened"}


class _IntakeOut(BaseModel):
    # only `route` drives the graph; keep the schema minimal
    route: str  # "firewall" | "skip"
    reason: str = ""


def run(state: BrainState) -> BrainState:
    ev = state.event
    if ev.action not in _ACTIONABLE:  # cheap deterministic gate before any llm call
        state.intake = IntakeResult(
            kind=ev.kind,
            relevant=False,
            route="skip",
            reason=f"action '{ev.action}' not actionable",
        )
        return state
    msg = [
        {
            "role": "system",
            "content": "You triage a GitHub event for a slop-firewall. The firewall's "
            "whole JOB is to "
            "evaluate low-quality, vague, terse, or suspicious issues and PRs, so do NOT skip "
            "something merely because it looks low-effort, vague, or bad -- that is exactly what "
            "the firewall must inspect. route='firewall' for any genuine human-authored issue or "
            "PR. route='skip' ONLY for non-content: automated bot comments, dependency-bump or CI "
            "chores, or clearly off-topic spam. When in doubt, choose 'firewall'.",
        },
        {"role": "user", "content": f"kind={ev.kind} title={ev.title}\n{ev.body}"},
    ]
    out = llm(msg, schema=_IntakeOut, role="intake")
    route = "firewall" if out.route == "firewall" else "skip"
    state.intake = IntakeResult(
        kind=ev.kind,
        relevant=route == "firewall",
        route=route,
        reason=out.reason,
    )
    return state
