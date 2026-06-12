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
    # parseable repo yields AST_TREE_SITTER; a single missing symbol downgrades
    # confidence to LOW regardless of engine grade
    ev = _pr(body="This wires up `validateToken()` before login.", changed_files=["src/auth.py"])
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.engine == "AST_TREE_SITTER"
    assert f.confidence == "LOW"


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


def test_cited_symbols_filters_conventional_commit_prefix():
    # a backtick-quoted commit subject like `fix(api): add endpoint` must not be
    # read as a citation of the function `fix` -- it is a conventional-commit prefix
    ev = _pr(body="See `fix(api): add endpoint` for context")
    assert checks.cited_symbols_exist(ev, _ctx(ev)).result == "pass"


def test_diff_identifiers_includes_removed_lines():
    # a symbol on a removed line is part of the diff and is not a hallucinated citation
    diff = (
        "--- a/src/auth.py\n"
        "+++ b/src/auth.py\n"
        "@@\n"
        "-def validateToken(tok):\n"
        "-    return True\n"
        "+def validateToken(tok):\n"
        "+    return False\n"
    )
    idents = checks.diff_identifiers(diff)
    assert "validateToken" in idents


def test_diff_identifiers_includes_context_lines():
    # a symbol on a context line (no +/-) is part of the diff and is not a hallucinated citation
    diff = "--- a/src/auth.py\n+++ b/src/auth.py\n@@\n def contextSymbol():\n+    pass\n"
    idents = checks.diff_identifiers(diff)
    assert "contextSymbol" in idents


def test_cited_symbols_single_missing_downgrades_to_low():
    # one unresolved citation is weak evidence; the confidence is capped at LOW
    # regardless of how well the repo is indexed
    ev = _pr(
        body="This calls `validateToken()` to fix it",
        changed_files=["src/auth.py"],
    )
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "LOW"


def test_cited_symbols_two_missing_keeps_graded_confidence():
    # two distinct unresolved citations retain the index-graded confidence
    ev = _pr(
        body="This calls `validateToken()` and `parseConfg()` to fix it",
        changed_files=["src/auth.py"],
    )
    f = checks.cited_symbols_exist(ev, _ctx(ev))
    assert f.result == "fail"
    # the fixture repo is parseable, so the engine grades HIGH for .py files
    assert f.confidence == "HIGH"


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


# --- template_untouched ---

_TEMPLATE_BODY = """\
<!-- Please fill in the description below -->
## What does this PR do?
<!-- Explain your changes here -->
## How was it tested?
- [ ] Unit tests
- [ ] Manual testing
---
"""

_BIG_DIFF = "\n".join(["+line " + str(i) for i in range(60)])
_SMALL_DIFF = "\n".join(["+line " + str(i) for i in range(5)])


def test_template_untouched_empty_template_big_diff_fails_high():
    ev = _pr(body=_TEMPLATE_BODY, diff=_BIG_DIFF)
    f = checks.template_untouched(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "HIGH"


def test_template_untouched_empty_template_small_diff_fails_low():
    ev = _pr(body=_TEMPLATE_BODY, diff=_SMALL_DIFF)
    f = checks.template_untouched(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "LOW"


def test_template_untouched_real_description_passes():
    ev = _pr(
        body="This PR refactors the auth module to remove the legacy token path. "
        "Tested manually against the dev environment."
    )
    assert checks.template_untouched(ev, _ctx(ev)).result == "pass"


def test_template_untouched_issue_reference_is_prose():
    # `#123` is an issue reference, not a markdown heading; it counts as content
    ev = _pr(body="Fixes #1234 by reordering the shutdown hooks", diff=_SMALL_DIFF)
    assert checks.template_untouched(ev, _ctx(ev)).result == "pass"


def test_template_untouched_checked_box_counts_as_content():
    # a checked box with an annotation shows the author interacted with the template
    ev = _pr(
        body=_TEMPLATE_BODY + "- [x] ran the full suite on linux and arm64 runners\n",
        diff=_BIG_DIFF,
    )
    assert checks.template_untouched(ev, _ctx(ev)).result == "pass"


def test_bare_link_line_rejects_adversarial_input_quickly():
    # token-by-token matching keeps an adversarial near-miss line linear; a
    # repetition over the whole line would backtrack exponentially here
    import time

    line = "http://a " * 40 + "["
    started = time.perf_counter()
    checks._is_bare_link_line(line)
    assert time.perf_counter() - started < 0.1


# --- verification_claims_unsupported ---


def test_verification_claims_added_tests_no_test_files_fails_high():
    ev = _pr(
        body="Added unit tests for the parser",
        changed_files=["src/parser.py"],
        ci_status=None,
    )
    f = checks.verification_claims_unsupported(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "HIGH"


def test_verification_claims_ran_verification_no_ci_no_tests_fails_low():
    ev = _pr(
        body="All tests pass (117/117), lint clean",
        changed_files=["src/parser.py"],
        ci_status="none",
    )
    f = checks.verification_claims_unsupported(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "LOW"


def test_verification_claims_ran_verification_ci_success_passes():
    ev = _pr(
        body="All tests pass (117/117), lint clean",
        changed_files=["src/parser.py"],
        ci_status="success",
    )
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_no_claims_passes():
    ev = _pr(body="Refactors the parser to use the new API.")
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_added_tests_with_test_file_passes():
    ev = _pr(
        body="Added unit tests for the parser",
        changed_files=["tests/test_parser.py"],
        ci_status=None,
    )
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_instruction_is_not_a_claim():
    # template/instruction phrasing is not the author claiming completed work
    ev = _pr(body="Please make sure all tests pass before requesting review.")
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_negation_is_not_a_claim():
    ev = _pr(body="I have not verified locally; CI should cover it.")
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_html_comment_is_not_a_claim():
    # template instructions live in HTML comments and are not the author's words
    ev = _pr(body="<!-- Confirm all tests pass before submitting -->\nSmall doc fix.")
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_cross_clause_mention_is_not_a_claim():
    # "adds ... tests" must not match across a clause boundary when the PR is
    # not actually claiming to add tests
    ev = _pr(body="Adds a config option that existing downstream integration tests rely on.")
    assert checks.verification_claims_unsupported(ev, _ctx(ev)).result == "pass"


def test_verification_claims_failing_ci_contradicts_claim():
    # a verification claim with CI red is contradicted, not merely uncorroborated
    ev = _pr(body="All tests pass locally", ci_status="failure")
    f = checks.verification_claims_unsupported(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "HIGH"
    assert "CI is failing" in f.evidence


# --- claimed_bug_exists ---


def test_claimed_bug_exists_contradicted_fails_llm(monkeypatch):
    monkeypatch.setattr(
        checks,
        "llm",
        lambda m, schema, **kw: schema(
            is_bug_fix_claim=True,
            contradicted=True,
            reason="the guard described as missing is already present in removed lines",
        ),
    )
    ev = _pr(
        body="Fix: missing null guard in parse()",
        diff="--- a/x.py\n+++ b/x.py\n-    if x is None: return\n+    if x is None: return None\n",
    )
    f = checks.claimed_bug_exists(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.engine == "LLM"
    assert "guard" in f.evidence


def test_claimed_bug_exists_not_bug_fix_unknown(monkeypatch):
    monkeypatch.setattr(
        checks,
        "llm",
        lambda m, schema, **kw: schema(
            is_bug_fix_claim=False,
            contradicted=False,
            reason="description adds a new feature, not a fix",
        ),
    )
    ev = _pr(
        body="Add dark mode toggle to the settings panel",
        diff="--- a/x.py\n+++ b/x.py\n+toggle = True\n",
    )
    f = checks.claimed_bug_exists(ev, _ctx(ev))
    assert f.result == "unknown"
    assert "does not claim a bug fix" in f.evidence


def test_claimed_bug_exists_no_diff_unknown():
    ev = _pr(body="Fix: missing null guard", diff=None)
    f = checks.claimed_bug_exists(ev, _ctx(ev))
    assert f.result == "unknown"
    assert "no diff" in f.evidence


# --- truncated_diff ---


def test_truncated_diff_under_limit_unchanged():
    # a diff that fits within the limit must be returned byte-for-byte unchanged
    short = "+" + "x" * 100
    assert checks.truncated_diff(short) == short


def test_truncated_diff_over_limit_capped():
    # a diff that exceeds the limit must be cut to at most the limit characters
    # (before the appended marker line) so the total embedding stays bounded
    over = "+" + "y" * (checks._DIFF_PROMPT_LIMIT + 5000)
    result = checks.truncated_diff(over)
    assert len(result) > checks._DIFF_PROMPT_LIMIT  # marker adds a bit
    assert result[: checks._DIFF_PROMPT_LIMIT] == over[: checks._DIFF_PROMPT_LIMIT]


def test_truncated_diff_over_limit_ends_with_marker():
    # the truncation marker must appear at the end so the model knows the diff
    # was cut and how much was omitted
    over = "+" + "z" * (checks._DIFF_PROMPT_LIMIT + 1000)
    result = checks.truncated_diff(over)
    total = len(over)
    assert (
        f"[... diff truncated: {checks._DIFF_PROMPT_LIMIT} of {total} characters shown ...]"
        in result
    )


# --- first_contribution ---


def test_first_contribution_passes_known_author():
    ev = _pr(author={"login": "a", "account_age_days": 3000, "is_first_time_contributor": False})
    assert checks.first_contribution(ev, _ctx(ev)).result == "pass"


def test_first_contribution_ci_success_corroborates():
    # a first-time author whose change is backed by CI success needs no track record
    ev = _pr(
        author={"login": "a", "account_age_days": 30, "is_first_time_contributor": True},
        ci_status="success",
    )
    assert checks.first_contribution(ev, _ctx(ev)).result == "pass"


def test_first_contribution_uncorroborated_fails_low():
    # no track record and nothing verifiable backs the claims -- weak evidence only,
    # so the confidence is LOW and the Judge may at most ask for verification
    ev = _pr(author={"login": "a", "account_age_days": 30, "is_first_time_contributor": True})
    f = checks.first_contribution(ev, _ctx(ev))
    assert f.result == "fail"
    assert f.confidence == "LOW"
    assert f.engine == "DETERMINISTIC"


def test_touches_tests_matches_singular_test_dir():
    # repos that keep their suite under test/ (singular) must corroborate
    # added-tests claims just like tests/
    assert checks._touches_tests(["test/app.options.js"])
