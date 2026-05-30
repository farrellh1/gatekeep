from __future__ import annotations

from core.schemas import BrainState, Action, GateInfo
from core.config import RepoConfig
from core.llm import llm


def draft_comment(state: BrainState) -> str:
    reasons = "\n".join(f"- {r}" for r in state.verdict.reasons)
    author = state.event.author.login
    msg = [
        {
            "role": "system",
            "content": "Write the final body of a short, polite, specific GitHub comment "
            "explaining the concern. Open by greeting the author as @<their handle>. Reference "
            "the concrete evidence. Output only the comment text — no markdown code fences and no "
            "placeholder brackets. Never accuse the author of using AI.",
        },
        {"role": "user", "content": f"Author handle: {author}\nConcerns:\n{reasons}"},
    ]
    return llm(msg)


def _draft(state: BrainState, config: RepoConfig) -> list[Action]:
    label = state.verdict.label
    if label == "slop":
        return [
            Action(action="comment", body=draft_comment(state)),
            Action(action="label", body=config.labels["slop"]),
            Action(action="close"),
        ]
    if label == "needs-info":
        return [
            Action(action="comment", body=draft_comment(state)),
            Action(action="label", body=config.labels["needs_info"]),
        ]
    return []


def run(state: BrainState, config: RepoConfig) -> BrainState:
    allow_close = config.mode == "auto-gate" and state.verdict.confidence >= config.threshold

    kept, gated = [], []
    for action in _draft(state, config):
        if action.action == "close" and not allow_close:
            gated.append(action.action)
        else:
            kept.append(action)

    state.actions = kept
    state.gate = GateInfo(
        policy=config.mode,
        gated=gated,
        reason=f"{state.verdict.label} @ {state.verdict.confidence:.2f}",
    )
    return state
