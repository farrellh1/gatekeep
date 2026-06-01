from pathlib import Path

from core.registry import REGISTRY, suite_for
from core.run_context import RunContext
from core.schemas import NormalizedEvent

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


def test_ci_status_check_reports_under_registry_name():
    # a check's Finding.check is its registry name, not the function's __name__
    ci = next(c for c in REGISTRY if c.name == "ci_status")
    ev = _ev(ci_status="failure")
    finding = ci.run(ev, RunContext(ev))
    assert finding.check == "ci_status"  # registry composes the name onto the CheckResult
    assert finding.result == "fail"


def test_cited_symbols_exist_runs_on_both_kinds():
    pr_names = {c.name for c in suite_for("pull_request")}
    issue_names = {c.name for c in suite_for("issue")}
    assert "cited_symbols_exist" in pr_names
    assert "cited_symbols_exist" in issue_names


def test_suite_membership_separates_pr_and_issue_checks():
    pr_names = {c.name for c in suite_for("pull_request")}
    issue_names = {c.name for c in suite_for("issue")}
    # PR-only checks never appear in an issue suite, and vice versa
    assert "ci_status" in pr_names and "ci_status" not in issue_names
    assert "has_repro" in issue_names and "has_repro" not in pr_names
    assert "is_duplicate" in issue_names and "is_duplicate" not in pr_names
