from __future__ import annotations

from brain.schemas import BrainState
from brain.config import RepoConfig
from brain import checks

PR_CHECKS = [
    checks.cited_symbols_exist,
    checks.touches_real_files,
    checks.cosmetic_only,
    checks.ci_status_check,
    checks.diff_matches_description,
]
ISSUE_CHECKS = [
    checks.cited_symbols_exist,
    checks.is_duplicate,
    checks.has_repro,
]


def run(state: BrainState, config: RepoConfig) -> BrainState:
    """Gather evidence: run the applicable check suite, record findings.

    The Investigator GATHERS -- it has no opinion and never sets a verdict.
    That discipline keeps judgment in exactly one place (the Judge).
    """
    suite = PR_CHECKS if state.event.kind == "pull_request" else ISSUE_CHECKS
    for check_fn in suite:
        if not config.is_check_enabled(check_fn.__name__):
            continue
        state.findings.append(check_fn(state.event))
    return state
