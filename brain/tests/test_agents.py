from pathlib import Path

from core.agents import intake as intake_mod
from core.agents import investigator as inv_mod
from core.agents import judge as judge_mod
from core.config import RepoConfig
from core.registry import Check
from core.schemas import BrainState, Finding, NormalizedEvent

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _ev(**kw):
    base = dict(
        delivery_id="d",
        kind="pull_request",
        action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1,
        title="t",
        body="b",
        author={"login": "a", "account_age_days": 10, "is_first_time_contributor": True},
    )
    base.update(kw)
    return NormalizedEvent(**base)


# --- Intake (Task 7) ---


def test_intake_routes_opened_pr_to_firewall(monkeypatch):
    monkeypatch.setattr(
        intake_mod,
        "llm",
        lambda messages, schema, **kw: schema(relevant=True, route="firewall", reason="new PR"),
    )
    out = intake_mod.run(BrainState(event=_ev()))
    assert out.intake.route == "firewall"


def test_intake_survives_model_omitting_fields(monkeypatch):
    # the real failure mode: the model returned {route, reason} with no `relevant`.
    # _IntakeOut no longer requires it, so the run must not crash and derives relevant.
    monkeypatch.setattr(
        intake_mod,
        "llm",
        lambda messages, schema, **kw: schema(route="firewall"),
    )
    out = intake_mod.run(BrainState(event=_ev()))
    assert out.intake.route == "firewall"
    assert out.intake.relevant is True


def test_intake_skips_irrelevant_action():
    # 'labeled' is not actionable -> skip without spending an llm call
    out = intake_mod.run(BrainState(event=_ev(action="labeled")))
    assert out.intake.route == "skip"


# --- Investigator (Task 8) ---


def _stub_check(name):
    return Check(
        name=name,
        kinds=("pull_request",),
        run=lambda ev: Finding(check=name, result="fail", evidence="x"),
    )


def test_investigator_runs_pr_checks(monkeypatch):
    monkeypatch.setattr(inv_mod, "suite_for", lambda kind: [_stub_check("cited_symbols_exist")])
    out = inv_mod.run(BrainState(event=_ev()), RepoConfig())
    assert len(out.findings) == 1
    assert out.findings[0].check == "cited_symbols_exist"


def test_investigator_respects_disabled_check(monkeypatch):
    monkeypatch.setattr(inv_mod, "suite_for", lambda kind: [_stub_check("cosmetic_only")])
    cfg = RepoConfig(checks={"cosmetic_only": False})
    out = inv_mod.run(BrainState(event=_ev()), cfg)
    assert out.findings == []


def test_ci_status_false_disables_ci_check_regression():
    # disabling a check by its registry name removes its Finding. A bare PR (no
    # diff/files) short-circuits every other check before any llm call.
    ev = _ev(ci_status="failure")
    enabled = inv_mod.run(BrainState(event=ev), RepoConfig())
    assert any(f.check == "ci_status" for f in enabled.findings)  # runs by default

    cfg = RepoConfig(checks={"ci_status": False})
    disabled = inv_mod.run(BrainState(event=ev), cfg)
    assert not any(f.check == "ci_status" for f in disabled.findings)  # disabled


# --- Judge (Task 9) ---


def test_judge_reads_findings_only(monkeypatch):
    captured = {}

    def fake_llm(messages, schema, **kwargs):
        captured["prompt"] = messages[-1]["content"]
        return schema(
            label="slop",
            confidence=0.92,
            reasons=["cites missing foo()"],
            primary_evidence="cited_symbols_exist",
        )

    monkeypatch.setattr(judge_mod, "llm", fake_llm)
    st = BrainState(event=_ev())
    st.findings = [Finding(check="cited_symbols_exist", result="fail", evidence="missing foo()")]
    out = judge_mod.run(st)
    assert out.verdict.label == "slop"
    assert "missing foo()" in captured["prompt"]  # judge saw the evidence, nothing else
