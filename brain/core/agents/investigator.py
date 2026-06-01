from __future__ import annotations

from core.config import RepoConfig
from core.registry import suite_for
from core.schemas import BrainState


def run(state: BrainState, config: RepoConfig) -> BrainState:
    """Gather evidence: run the applicable check suite, record findings.

    The Investigator GATHERS -- it has no opinion and never sets a verdict.
    That discipline keeps judgment in exactly one place (the Judge).

    The suite is the registry filtered by event kind; each check is gated on its
    registry name -- the same name it reports -- so config keys cannot drift.
    """
    for check in suite_for(state.event.kind):
        if not config.is_check_enabled(check.name):
            continue
        state.findings.append(check.run(state.event))
    return state
