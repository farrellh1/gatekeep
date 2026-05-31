from pathlib import Path

import core.checks as checks
from core import graph
from core.agents import intake, judge, responder
from core.schemas import Verdict

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _event_dict(**kw):
    d = dict(
        delivery_id="d",
        kind="pull_request",
        action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1,
        title="Fix auth",
        body="calls `validateToken()` to fix",
        author={"login": "a", "account_age_days": 2, "is_first_time_contributor": True},
        diff="--- a/x\n+++ b/x\n+# noop\n",
        changed_files=["src/auth.py"],
        ci_status="failure",
    )
    d.update(kw)
    return d


def test_process_slop_pr_end_to_end(monkeypatch):
    monkeypatch.setattr(
        intake,
        "llm",
        lambda messages, schema: schema(relevant=True, route="firewall", reason="new PR"),
    )
    monkeypatch.setattr(
        checks, "llm", lambda messages, schema: schema(mismatch=True, reason="noop")
    )
    monkeypatch.setattr(
        judge,
        "llm",
        lambda messages, schema: Verdict(
            label="slop",
            confidence=0.95,
            reasons=["cites validateToken()"],
            primary_evidence="cited_symbols_exist",
        ),
    )
    monkeypatch.setattr(responder, "llm", lambda messages: "polite note")

    result = graph.process(_event_dict(), config_yaml=None)
    assert result["verdict"]["label"] == "slop"
    assert "close" not in {a["action"] for a in result["actions"]}
    assert any(a["action"] == "comment" for a in result["actions"])


def test_process_skips_irrelevant():
    result = graph.process(_event_dict(action="labeled"), config_yaml=None)
    assert result["intake"]["route"] == "skip"
    assert result["actions"] == []
