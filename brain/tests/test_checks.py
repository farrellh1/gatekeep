from pathlib import Path

from core import checks
from core.run_context import RunContext
from core.schemas import NormalizedEvent

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _pr(**kw):
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


def _ctx(ev):
    return RunContext(ev)


# --- deterministic checks (Task 5) ---


def test_cited_symbols_flags_hallucinated():
    ev = _pr(body="This calls `validateToken()` to fix it")
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail"
    assert "validateToken" in f.evidence


def test_cited_symbols_passes_real():
    ev = _pr(body="touches `login()`")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_ignores_prose_parentheticals():
    # normal English with parentheticals must NOT be read as fabricated symbols
    ev = _pr(body="This works (mostly) and we tested it (twice). See foo (the old one).")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_ignores_unbackticked_prose_calls():
    # Option A: bare prose calls are not trusted, even if they look like symbols
    ev = _pr(body="we call validateToken() somewhere")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_ignores_control_flow_in_backticks():
    # the no-space-before-paren rule excludes `if (x)` by shape, no keyword list
    ev = _pr(body="guard with `if (ready)` before calling")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_flags_backtick_fabrication():
    # explicitly-marked code that doesn't exist is the true positive we want
    ev = _pr(body="This patch wires up `parseConfg()` to the loader.")
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail" and "parseConfg" in f.evidence


def test_cited_symbols_passes_real_backtick():
    ev = _pr(body="calls `login()` correctly")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_dotted_path_uses_last_segment():
    # `utils.login()` -> verifies `login`, which exists in the fixture
    ev = _pr(body="delegates to `utils.login()`")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_passes_symbol_added_in_diff():
    # the brain clones only the base branch, so a symbol the PR is adding is
    # absent from the clone -- it must not be read as a hallucination
    ev = _pr(
        body="This adds `calculateRewards()` to the rewards path.",
        diff=(
            "--- a/src/rewards.py\n"
            "+++ b/src/rewards.py\n"
            "@@\n"
            "+def calculateRewards(user):\n"
            "+    return 0\n"
        ),
    )
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_cited_symbols_flags_symbol_absent_from_repo_and_diff():
    # genuine hallucination: cited as part of the fix but nowhere in repo or diff
    ev = _pr(
        body="This wires up `validateToken()` before login.",
        diff=(
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@\n"
            "-    return True\n"
            "+    return True  # patched\n"
        ),
    )
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail" and "validateToken" in f.evidence


def test_cited_symbols_grades_engine_at_runtime():
    # the engine is graded on the result, not fixed by the registry: a fully
    # parseable repo yields AST_TREE_SITTER on a miss.
    ev = _pr(body="This wires up `validateToken()` before login.", changed_files=["src/auth.py"])
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail"
    assert (f.confidence, f.engine) == ("HIGH", "AST_TREE_SITTER")


def test_reader_built_once_per_event(monkeypatch):
    # checks share ctx.reader, so the tree-sitter index is built exactly once per
    # event and reused across calls -- not rebuilt on every check invocation.
    from core import repo_reader

    builds = {"n": 0}
    orig = repo_reader.RepoReader._build_index

    def counting_build(self):
        builds["n"] += 1
        return orig(self)

    monkeypatch.setattr(repo_reader.RepoReader, "_build_index", counting_build)

    ev = _pr(body="touches `login()`", changed_files=["src/auth.py"])
    ctx = _ctx(ev)
    checks.cited_symbols_exist(ev, ctx)
    # cited_symbols_exist builds the index once; a second call reuses the reader
    checks.cited_symbols_exist(ev, ctx)
    assert builds["n"] == 1


def test_cosmetic_only_flags_whitespace_diff():
    ev = _pr(diff="--- a/x\n+++ b/x\n-foo( )\n+foo()\n")
    assert checks.cosmetic_only(ev, _ctx(ev)).result == "fail"


def test_cosmetic_only_passes_real_change():
    ev = _pr(diff="--- a/x\n+++ b/x\n-return 1\n+return 2\n")
    assert checks.cosmetic_only(ev, _ctx(ev)).result == "pass"


def test_cosmetic_only_passes_significant_dedent():
    # leading indentation is behavior in whitespace-significant languages: pulling
    # `return None` out of the `if` block changes when it runs
    ev = _pr(
        diff=(
            "--- a/auth.py\n"
            "+++ b/auth.py\n"
            "@@\n"
            "     if not user:\n"
            "-        return None\n"
            "+    return None\n"
        ),
    )
    assert checks.cosmetic_only(ev, _ctx(ev)).result == "pass"


def test_cosmetic_only_passes_code_move_across_files():
    # a function cut from one file and pasted into another has matching add/remove
    # lines pooled together, but per file it is a deletion and an addition
    ev = _pr(
        diff=(
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@\n"
            "-def helper():\n"
            "-    return 1\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@\n"
            "+def helper():\n"
            "+    return 1\n"
        ),
    )
    assert checks.cosmetic_only(ev, _ctx(ev)).result == "pass"


def test_cosmetic_only_passes_statement_reorder():
    # swapping two statements is a behavior change, but the added and removed lines
    # are the same set -- only their order differs
    ev = _pr(
        diff=(
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@\n"
            "-    save(db)\n"
            "-    commit(db)\n"
            "+    commit(db)\n"
            "+    save(db)\n"
        ),
    )
    assert checks.cosmetic_only(ev, _ctx(ev)).result == "pass"


def test_ci_status_failure():
    ev = _pr(ci_status="failure")
    assert checks.ci_status_check(ev, _ctx(ev)).result == "fail"


def test_ci_status_unknown_when_absent():
    ev = _pr(ci_status="none")
    assert checks.ci_status_check(ev, _ctx(ev)).result == "unknown"


# --- llm-backed checks (Task 6); llm() is mocked ---


def test_diff_matches_description_flags_mismatch(monkeypatch):
    monkeypatch.setattr(
        checks,
        "llm",
        lambda m, schema, **kw: schema(mismatch=True, reason="body claims fix, diff is noop"),
    )
    ev = _pr(body="Fixes the auth bug", diff="--- a/r\n+++ b/r\n+# comment\n")
    f = checks.diff_matches_description(ev, _ctx(ev))
    assert f.result == "fail" and "noop" in f.evidence


def test_diff_matches_description_passes(monkeypatch):
    monkeypatch.setattr(
        checks,
        "llm",
        lambda messages, schema, **kw: schema(mismatch=False, reason="diff matches"),
    )
    ev = _pr(body="bump", diff="--- a/r\n+++ b/r\n+x=2\n")
    assert checks.diff_matches_description(ev, _ctx(ev)).result == "pass"


def test_has_repro_flags_missing(monkeypatch):
    monkeypatch.setattr(
        checks,
        "llm",
        lambda messages, schema, **kw: schema(has_repro=False, reason="no steps"),
    )
    ev = _pr(kind="issue", body="it doesn't work pls fix")
    assert checks.has_repro(ev, _ctx(ev)).result == "fail"


def test_is_duplicate_flags_match(monkeypatch):
    monkeypatch.setattr(
        checks,
        "llm",
        lambda messages, schema, **kw: schema(duplicate_of=7, reason="same crash"),
    )
    ev = _pr(
        kind="issue",
        body="crash on save",
        existing_issues=[{"number": 7, "title": "crash on save", "body": "..."}],
    )
    f = checks.is_duplicate(ev, _ctx(ev))
    assert f.result == "fail" and "#7" in f.evidence


def test_is_duplicate_unknown_without_candidates():
    ev = _pr(kind="issue", existing_issues=None)
    assert checks.is_duplicate(ev, _ctx(ev)).result == "unknown"
