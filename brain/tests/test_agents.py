from pathlib import Path

from core.schemas import NormalizedEvent, BrainState, Finding
from core.config import RepoConfig
from core.agents import intake as intake_mod
from core.agents import investigator as inv_mod
from core.agents import judge as judge_mod

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _ev(**kw):
    base = dict(
        delivery_id="d", kind="pull_request", action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1, title="t", body="b",
        author={"login": "a", "account_age_days": 10, "is_first_time_contributor": True},
    )
    base.update(kw)
    return NormalizedEvent(**base)


# --- Intake (Task 7) ---

def test_intake_routes_opened_pr_to_firewall(monkeypatch):
    monkeypatch.setattr(
        intake_mod, "llm",
        lambda messages, schema: schema(relevant=True, route="firewall", reason="new PR"),
    )
    out = intake_mod.run(BrainState(event=_ev()))
    assert out.intake.route == "firewall"


def test_intake_skips_irrelevant_action():
    # 'labeled' is not actionable -> skip without spending an llm call
    out = intake_mod.run(BrainState(event=_ev(action="labeled")))
    assert out.intake.route == "skip"


# --- Investigator (Task 8) ---

def test_investigator_runs_pr_checks(monkeypatch):
    monkeypatch.setattr(inv_mod, "PR_CHECKS", [
        lambda ev: Finding(check="cited_symbols_exist", result="fail", evidence="missing foo()"),
    ])
    out = inv_mod.run(BrainState(event=_ev()), RepoConfig())
    assert len(out.findings) == 1
    assert out.findings[0].check == "cited_symbols_exist"


def test_investigator_respects_disabled_check(monkeypatch):
    def f(ev):
        return Finding(check="cosmetic_only", result="fail", evidence="x")
    f.__name__ = "cosmetic_only"
    monkeypatch.setattr(inv_mod, "PR_CHECKS", [f])
    cfg = RepoConfig(checks={"cosmetic_only": False})
    out = inv_mod.run(BrainState(event=_ev()), cfg)
    assert out.findings == []


# --- Judge (Task 9) ---

def test_judge_reads_findings_only(monkeypatch):
    captured = {}

    def fake_llm(messages, schema):
        captured["prompt"] = messages[-1]["content"]
        return schema(
            label="slop", confidence=0.92,
            reasons=["cites missing foo()"], primary_evidence="cited_symbols_exist",
        )

    monkeypatch.setattr(judge_mod, "llm", fake_llm)
    st = BrainState(event=_ev())
    st.findings = [Finding(check="cited_symbols_exist", result="fail", evidence="missing foo()")]
    out = judge_mod.run(st)
    assert out.verdict.label == "slop"
    assert "missing foo()" in captured["prompt"]  # judge saw the evidence, nothing else
