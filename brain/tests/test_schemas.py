from core.schemas import Action, BrainState, CheckResult, NormalizedEvent


def test_normalized_event_minimal_issue():
    ev = NormalizedEvent(
        delivery_id="d1",
        kind="issue",
        action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": "/tmp/x"},
        number=5,
        title="bug",
        body="it breaks",
        author={"login": "alice", "account_age_days": 400, "is_first_time_contributor": False},
    )
    assert ev.kind == "issue"
    assert ev.diff is None


def test_brainstate_starts_empty():
    ev = NormalizedEvent(
        delivery_id="d1",
        kind="issue",
        action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": "/tmp/x"},
        number=5,
        title="bug",
        body="",
        author={"login": "a", "account_age_days": 1, "is_first_time_contributor": True},
    )
    st = BrainState(event=ev)
    assert st.findings == [] and st.verdict is None and st.actions == []


def test_action_close_has_no_body():
    a = Action(action="close")
    assert a.body is None


def test_check_result_is_nameless_with_finding_defaults():
    # a CheckResult carries the same shape as Finding minus the name; the registry
    # composes the name, so a check is structurally incapable of naming itself.
    r = CheckResult(result="fail", evidence="missing foo()")
    assert not hasattr(r, "check")
    assert r.confidence == "HIGH"  # same default as Finding
    assert r.engine == "DETERMINISTIC"  # same default as Finding
