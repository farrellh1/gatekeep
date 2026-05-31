from pathlib import Path

from core.schemas import NormalizedEvent, BrainState, Verdict, Finding
from core.config import RepoConfig
from core.agents import responder as resp_mod

CLONE = str(Path(__file__).parent / "fixtures" / "clone")

HIGH_FAIL = Finding(
    check="cited_symbols_exist", result="fail",
    evidence="references foo() which doesn't exist",
    confidence="HIGH", engine="AST_TREE_SITTER",
)


def _state(label, conf, findings=None):
    ev = NormalizedEvent(
        delivery_id="d", kind="pull_request", action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1, title="t", body="b",
        author={"login": "a", "account_age_days": 10, "is_first_time_contributor": True},
    )
    st = BrainState(event=ev)
    st.findings = findings or []
    st.verdict = Verdict(
        label=label, confidence=conf,
        reasons=["references foo() which doesn't exist"],
        primary_evidence="cited_symbols_exist",
    )
    return st


def test_suggest_only_strips_close(monkeypatch):
    monkeypatch.setattr(resp_mod, "draft_comment", lambda st: "polite explanation")
    out = resp_mod.run(_state("slop", 0.95), RepoConfig(mode="suggest-only"))
    kinds = {a.action for a in out.actions}
    assert "close" not in kinds
    assert "comment" in kinds and "label" in kinds
    assert "close" in out.gate.gated


def test_auto_gate_allows_close_above_threshold(monkeypatch):
    monkeypatch.setattr(resp_mod, "draft_comment", lambda st: "x")
    out = resp_mod.run(_state("slop", 0.95, [HIGH_FAIL]), RepoConfig(mode="auto-gate", threshold=0.85))
    assert "close" in {a.action for a in out.actions}


def test_auto_gate_strips_close_below_threshold(monkeypatch):
    monkeypatch.setattr(resp_mod, "draft_comment", lambda st: "x")
    out = resp_mod.run(_state("slop", 0.70, [HIGH_FAIL]), RepoConfig(mode="auto-gate", threshold=0.85))
    assert "close" not in {a.action for a in out.actions}


def test_auto_gate_withholds_close_without_high_confidence_evidence(monkeypatch):
    # a confident 'slop' verdict that no HIGH-confidence finding backs is likely an LLM
    # fluke; never auto-close on it -- comment/label only, leave the close for a human
    monkeypatch.setattr(resp_mod, "draft_comment", lambda st: "x")
    out = resp_mod.run(_state("slop", 0.95, []), RepoConfig(mode="auto-gate", threshold=0.85))
    assert "close" not in {a.action for a in out.actions}
    assert "close" in out.gate.gated


def test_legit_produces_no_actions(monkeypatch):
    monkeypatch.setattr(resp_mod, "draft_comment", lambda st: "x")
    out = resp_mod.run(_state("legit", 0.9), RepoConfig())
    assert out.actions == []
