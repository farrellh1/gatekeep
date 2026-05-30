from brain.schemas import NormalizedEvent, BrainState, Finding, Verdict, Action


def test_normalized_event_minimal_issue():
    ev = NormalizedEvent(
        delivery_id="d1", kind="issue", action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": "/tmp/x"},
        number=5, title="bug", body="it breaks",
        author={"login": "alice", "account_age_days": 400, "is_first_time_contributor": False},
    )
    assert ev.kind == "issue"
    assert ev.diff is None


def test_brainstate_starts_empty():
    ev = NormalizedEvent(
        delivery_id="d1", kind="issue", action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": "/tmp/x"},
        number=5, title="bug", body="",
        author={"login": "a", "account_age_days": 1, "is_first_time_contributor": True},
    )
    st = BrainState(event=ev)
    assert st.findings == [] and st.verdict is None and st.actions == []


def test_action_close_has_no_body():
    a = Action(action="close")
    assert a.body is None
