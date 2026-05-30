from __future__ import annotations

from pydantic import BaseModel

from brain.schemas import BrainState, IntakeResult
from brain.llm import llm

_ACTIONABLE = {"opened", "edited", "reopened"}


class _IntakeOut(BaseModel):
    relevant: bool
    route: str  # "firewall" | "skip"
    reason: str


def run(state: BrainState) -> BrainState:
    ev = state.event
    if ev.action not in _ACTIONABLE:  # cheap deterministic gate before any llm call
        state.intake = IntakeResult(
            kind=ev.kind, relevant=False, route="skip",
            reason=f"action '{ev.action}' not actionable",
        )
        return state
    msg = [
        {"role": "system", "content":
            "You triage a GitHub event for a slop-firewall. route='firewall' for a genuine "
            "new issue/PR worth checking; route='skip' for bot noise, chores, or off-topic."},
        {"role": "user", "content": f"kind={ev.kind} title={ev.title}\n{ev.body}"},
    ]
    out = llm(msg, schema=_IntakeOut)
    state.intake = IntakeResult(
        kind=ev.kind, relevant=out.relevant,
        route="firewall" if out.route == "firewall" else "skip", reason=out.reason,
    )
    return state
