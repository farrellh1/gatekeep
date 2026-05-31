from pathlib import Path

from core.schemas import NormalizedEvent
from core import checks

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _pr(**kw):
    base = dict(
        delivery_id="d", kind="pull_request", action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1, title="t", body="b",
        author={"login": "a", "account_age_days": 10, "is_first_time_contributor": True},
    )
    base.update(kw)
    return NormalizedEvent(**base)


# --- deterministic checks (Task 5) ---

def test_cited_symbols_flags_hallucinated():
    ev = _pr(body="This calls `validateToken()` to fix it")
    f = checks.cited_symbols_exist(ev)
    assert f.result == "fail"
    assert "validateToken" in f.evidence


def test_cited_symbols_passes_real():
    ev = _pr(body="touches `login()`")
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_ignores_prose_parentheticals():
    # normal English with parentheticals must NOT be read as fabricated symbols
    ev = _pr(body="This works (mostly) and we tested it (twice). See foo (the old one).")
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_ignores_unbackticked_prose_calls():
    # Option A: bare prose calls are not trusted, even if they look like symbols
    ev = _pr(body="we call validateToken() somewhere")
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_ignores_control_flow_in_backticks():
    # the no-space-before-paren rule excludes `if (x)` by shape, no keyword list
    ev = _pr(body="guard with `if (ready)` before calling")
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_flags_backtick_fabrication():
    # explicitly-marked code that doesn't exist is the true positive we want
    ev = _pr(body="This patch wires up `parseConfg()` to the loader.")
    f = checks.cited_symbols_exist(ev)
    assert f.result == "fail" and "parseConfg" in f.evidence


def test_cited_symbols_passes_real_backtick():
    ev = _pr(body="calls `login()` correctly")
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_dotted_path_uses_last_segment():
    # `utils.login()` -> verifies `login`, which exists in the fixture
    ev = _pr(body="delegates to `utils.login()`")
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_passes_symbol_added_in_diff():
    # the brain clones only the base branch, so a symbol the PR is adding is
    # absent from the clone -- it must not be read as a hallucination
    ev = _pr(
        body="This adds `calculateRewards()` to the rewards path.",
        diff="--- a/src/rewards.py\n+++ b/src/rewards.py\n@@\n+def calculateRewards(user):\n+    return 0\n",
    )
    assert checks.cited_symbols_exist(ev).result == "pass"


def test_cited_symbols_flags_symbol_absent_from_repo_and_diff():
    # genuine hallucination: cited as part of the fix but nowhere in repo or diff
    ev = _pr(
        body="This wires up `validateToken()` before login.",
        diff="--- a/src/auth.py\n+++ b/src/auth.py\n@@\n-    return True\n+    return True  # patched\n",
    )
    f = checks.cited_symbols_exist(ev)
    assert f.result == "fail" and "validateToken" in f.evidence


def test_touches_real_files_flags_missing():
    ev = _pr(changed_files=["src/nope.py"])
    assert checks.touches_real_files(ev).result == "fail"


def test_touches_real_files_passes_when_some_exist():
    # a PR adding a new file alongside an existing one is legit, not slop
    ev = _pr(changed_files=["src/auth.py", "src/new_feature.py"])
    assert checks.touches_real_files(ev).result == "pass"


def test_cosmetic_only_flags_whitespace_diff():
    ev = _pr(diff="--- a/x\n+++ b/x\n-foo( )\n+foo()\n")
    assert checks.cosmetic_only(ev).result == "fail"


def test_cosmetic_only_passes_real_change():
    ev = _pr(diff="--- a/x\n+++ b/x\n-return 1\n+return 2\n")
    assert checks.cosmetic_only(ev).result == "pass"


def test_ci_status_failure():
    ev = _pr(ci_status="failure")
    assert checks.ci_status_check(ev).result == "fail"


def test_ci_status_unknown_when_absent():
    ev = _pr(ci_status="none")
    assert checks.ci_status_check(ev).result == "unknown"


# --- llm-backed checks (Task 6); llm() is mocked ---

def test_diff_matches_description_flags_mismatch(monkeypatch):
    monkeypatch.setattr(
        checks, "llm",
        lambda messages, schema: schema(mismatch=True, reason="body claims fix, diff is noop"),
    )
    ev = _pr(body="Fixes the auth bug", diff="--- a/r\n+++ b/r\n+# comment\n")
    f = checks.diff_matches_description(ev)
    assert f.result == "fail" and "noop" in f.evidence


def test_diff_matches_description_passes(monkeypatch):
    monkeypatch.setattr(
        checks, "llm",
        lambda messages, schema: schema(mismatch=False, reason="diff matches"),
    )
    ev = _pr(body="bump", diff="--- a/r\n+++ b/r\n+x=2\n")
    assert checks.diff_matches_description(ev).result == "pass"


def test_has_repro_flags_missing(monkeypatch):
    monkeypatch.setattr(
        checks, "llm",
        lambda messages, schema: schema(has_repro=False, reason="no steps"),
    )
    ev = _pr(kind="issue", body="it doesn't work pls fix")
    assert checks.has_repro(ev).result == "fail"


def test_is_duplicate_flags_match(monkeypatch):
    monkeypatch.setattr(
        checks, "llm",
        lambda messages, schema: schema(duplicate_of=7, reason="same crash"),
    )
    ev = _pr(
        kind="issue", body="crash on save",
        existing_issues=[{"number": 7, "title": "crash on save", "body": "..."}],
    )
    f = checks.is_duplicate(ev)
    assert f.result == "fail" and "#7" in f.evidence


def test_is_duplicate_unknown_without_candidates():
    ev = _pr(kind="issue", existing_issues=None)
    assert checks.is_duplicate(ev).result == "unknown"
