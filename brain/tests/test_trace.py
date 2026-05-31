import json
from pathlib import Path

import pytest

from core import graph
from core.agents import intake, judge, responder
from core.schemas import Verdict
import core.checks as checks

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _event_dict(**kw):
    d = dict(
        delivery_id="trace-1", kind="pull_request", action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1, title="Fix auth", body="calls `validateToken()` to fix",
        author={"login": "a", "account_age_days": 2, "is_first_time_contributor": True},
        diff="--- a/x\n+++ b/x\n+# noop\n", changed_files=["src/auth.py"], ci_status="failure",
    )
    d.update(kw)
    return d


def _patch_all(monkeypatch):
    monkeypatch.setattr(intake, "llm",
        lambda messages, schema: schema(relevant=True, route="firewall", reason="new PR"))
    monkeypatch.setattr(checks, "llm",
        lambda messages, schema: schema(mismatch=True, reason="noop"))
    monkeypatch.setattr(judge, "llm", lambda messages, schema:
        Verdict(label="slop", confidence=0.95, reasons=["cites validateToken()"],
                primary_evidence="cited_symbols_exist"))
    monkeypatch.setattr(responder, "llm", lambda messages: "polite note")


def test_trace_records_full_handoff_in_order(monkeypatch):
    _patch_all(monkeypatch)
    result = graph.process(_event_dict(), config_yaml=None)
    nodes = [s["node"] for s in result["trace"]["steps"]]
    assert nodes == ["intake", "investigator", "judge", "responder"]


def test_trace_inline_shape_matches_file(monkeypatch):
    _patch_all(monkeypatch)
    trace = graph.process(_event_dict(delivery_id="shape-1"), config_yaml=None)["trace"]
    assert trace["delivery_id"] == "shape-1"
    assert {"delivery_id", "kind", "number", "steps"} <= trace.keys()


def test_trace_steps_carry_reasoning_and_timing(monkeypatch):
    _patch_all(monkeypatch)
    steps = graph.process(_event_dict(), config_yaml=None)["trace"]["steps"]
    judge_step = next(s for s in steps if s["node"] == "judge")
    assert judge_step["output"]["verdict"]["label"] == "slop"
    assert "validateToken" in judge_step["summary"]
    assert all(isinstance(s["elapsed_ms"], (int, float)) for s in steps)


def test_trace_stops_at_intake_on_skip():
    # non-actionable action -> intake skips deterministically; later nodes never run
    steps = graph.process(_event_dict(action="labeled"), config_yaml=None)["trace"]["steps"]
    assert [s["node"] for s in steps] == ["intake"]
    assert steps[0]["output"]["intake"]["route"] == "skip"


def test_trace_captures_node_error_and_persists(monkeypatch, tmp_path):
    # a node raises mid-pipeline: the trace must name the failing node + the
    # exception and still be written to disk.
    _patch_all(monkeypatch)
    monkeypatch.setattr(judge, "llm",
        lambda messages, schema: (_ for _ in ()).throw(ValueError("structured output broke")))
    monkeypatch.setenv("GATEKEEP_TRACE_DIR", str(tmp_path))

    with pytest.raises(ValueError):
        graph.process(_event_dict(delivery_id="err-1"), config_yaml=None)

    data = json.loads((tmp_path / "err-1.json").read_text())
    assert [s["node"] for s in data["steps"]] == ["intake", "investigator", "judge"]
    assert "structured output broke" in data["steps"][-1]["error"]


def test_trace_written_to_disk_when_dir_set(monkeypatch, tmp_path):
    _patch_all(monkeypatch)
    monkeypatch.setenv("GATEKEEP_TRACE_DIR", str(tmp_path))
    graph.process(_event_dict(delivery_id="written-1"), config_yaml=None)
    written = tmp_path / "written-1.json"
    assert written.is_file()
    import json
    data = json.loads(written.read_text())
    assert data["delivery_id"] == "written-1"
    assert [s["node"] for s in data["steps"]] == ["intake", "investigator", "judge", "responder"]
