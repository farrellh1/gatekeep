from fastapi.testclient import TestClient

import server

client = TestClient(server.app)

_EVENT = {
    "delivery_id": "d1",
    "kind": "pull_request",
    "action": "opened",
    "repo": {"owner": "o", "name": "r", "default_branch": "main", "clone_path": "/tmp/x"},
    "number": 1,
    "title": "t",
    "body": "b",
    "author": {"login": "a", "account_age_days": 5, "is_first_time_contributor": True},
}


def test_process_endpoint_returns_brainstate(monkeypatch):
    monkeypatch.setattr(
        server,
        "process",
        lambda event_dict, config_yaml: {"verdict": {"label": "slop"}, "actions": []},
    )
    r = client.post("/process", json={"event": _EVENT, "config_yaml": None})
    assert r.status_code == 200
    assert r.json()["verdict"]["label"] == "slop"


def test_malformed_event_returns_422():
    r = client.post("/process", json={"event": {"kind": "pull_request"}, "config_yaml": None})
    assert r.status_code == 422
