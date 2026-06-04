from __future__ import annotations

import os
import time

from langgraph.graph import END, StateGraph

from core.agents import intake, investigator, judge, responder
from core.config import RepoConfig, load_config
from core.schemas import BrainState, NormalizedEvent
from core.trace import Tracer


def _route(state: BrainState) -> str:
    return "skip" if state.intake.route == "skip" else "go"


def _traced(name, fn, tracer: Tracer):
    def wrapper(state: BrainState) -> BrainState:
        t0 = time.perf_counter()
        try:
            out = fn(state)
        except Exception as exc:
            tracer.record_error(name, exc, (time.perf_counter() - t0) * 1000)
            raise
        tracer.record(name, out, (time.perf_counter() - t0) * 1000)
        return out

    return wrapper


def _build(config: RepoConfig, tracer: Tracer, reader=None):
    g = StateGraph(BrainState)
    g.add_node("intake", _traced("intake", intake.run, tracer))
    g.add_node(
        "investigator",
        _traced("investigator", lambda s: investigator.run(s, config, reader), tracer),
    )
    g.add_node("judge", _traced("judge", judge.run, tracer))
    g.add_node("responder", _traced("responder", lambda s: responder.run(s, config), tracer))

    g.set_entry_point("intake")
    g.add_conditional_edges("intake", _route, {"skip": END, "go": "investigator"})
    g.add_edge("investigator", "judge")
    g.add_edge("judge", "responder")
    g.add_edge("responder", END)
    return g.compile()


def process(event_dict: dict, config_yaml: str | None, reader=None) -> dict:
    config = load_config(config_yaml)
    event = NormalizedEvent(**event_dict)
    tracer = Tracer(event)
    trace_dir = os.environ.get("GATEKEEP_TRACE_DIR")
    try:
        final = _build(config, tracer, reader).invoke(BrainState(event=event))
    except Exception:
        if trace_dir:  # persist the partial trace (with the error step) before re-raising
            tracer.write(trace_dir)
        raise
    state = final if isinstance(final, BrainState) else BrainState(**final)
    out = state.model_dump()
    out["trace"] = tracer.to_dict()  # same shape as the persisted trace file
    if trace_dir:
        tracer.write(trace_dir)
    return out
