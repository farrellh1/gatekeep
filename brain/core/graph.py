from __future__ import annotations

from langgraph.graph import StateGraph, END

from core.schemas import BrainState, NormalizedEvent
from core.config import load_config, RepoConfig
from core.agents import intake, investigator, judge, responder


def _route(state: BrainState) -> str:
    return "skip" if state.intake.route == "skip" else "go"


def _build(config: RepoConfig):
    g = StateGraph(BrainState)
    g.add_node("intake", intake.run)
    g.add_node("investigator", lambda s: investigator.run(s, config))
    g.add_node("judge", judge.run)
    g.add_node("responder", lambda s: responder.run(s, config))

    g.set_entry_point("intake")
    g.add_conditional_edges("intake", _route, {"skip": END, "go": "investigator"})
    g.add_edge("investigator", "judge")
    g.add_edge("judge", "responder")
    g.add_edge("responder", END)
    return g.compile()


def process(event_dict: dict, config_yaml: str | None) -> dict:
    config = load_config(config_yaml)
    event = NormalizedEvent(**event_dict)
    final = _build(config).invoke(BrainState(event=event))
    state = final if isinstance(final, BrainState) else BrainState(**final)
    return state.model_dump()
