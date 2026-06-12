from __future__ import annotations

from core.llm import llm
from core.schemas import BrainState, Verdict


def run(state: BrainState) -> BrainState:
    """Weigh ONLY the findings into a verdict. Does not gather, does not act.

    The Judge sees the check findings and nothing else -- given the same
    findings, the verdict is reproducible and explainable (the audit claim).
    """
    findings = "\n".join(
        f"- [{f.result}] (confidence={f.confidence}, engine={f.engine}) {f.check}: {f.evidence}"
        for f in state.findings
    )
    msg = [
        {
            "role": "system",
            "content": "You are the Judge. Given ONLY these check findings, decide: "
            "label is 'slop' "
            "(clear invalidity/hallucination/no-op), 'needs-info' (likely real but missing "
            "repro/detail), or 'legit' (passes). Be conservative: prefer 'legit' or "
            "'needs-info' unless evidence of slop is strong. confidence in [0,1]. "
            "Cite the findings in reasons.\n\n"
            "Each finding carries a confidence (HIGH/LOW) and the engine that produced it. "
            "HIGH-confidence fails carry full weight. A HIGH-confidence cited_symbols_exist fail "
            "with MULTIPLE missing symbols, or one corroborated by another HIGH-confidence failing "
            "check, is strong evidence of hallucinated references and warrants 'slop'. A single "
            "missing symbol on its own may be an external library, syscall, or dependency name the "
            "repo index cannot see -- cap that at 'needs-info' unless corroborated by another "
            "HIGH-confidence fail.\n"
            "Do No Harm applies to weak evidence: a 'fail' with LOW confidence is weak by its own "
            "check's admission -- a degraded language path we could not fully parse "
            "(engine=HEURISTIC), or a signal that is suspicious but not provably false "
            "(engine=DETERMINISTIC). A LOW-confidence fail MUST NOT by itself justify a "
            "'slop' label; "
            "treat it as at most 'needs-info' unless an independent HIGH-confidence "
            "finding confirms "
            "the problem.\n"
            "engine=LLM findings are one model's reading of the text, not deterministic facts. "
            "A single failing engine=LLM check on its own justifies at most 'needs-info'; "
            "labelling 'slop' on LLM evidence requires either two independent failing engine=LLM "
            "checks, or one engine=LLM fail corroborated by a deterministic or AST failure "
            "(engine=DETERMINISTIC or engine=AST_TREE_SITTER).\n"
            "The first_contribution finding is a prior, not evidence of wrongdoing: it MUST "
            "NEVER contribute to a 'slop' label, alone or as corroboration. Its only effect: "
            "when it fails AND the PR's claims have no independent corroboration (no CI "
            "success, no test changes, nothing verifiable), prefer 'needs-info' over 'legit' "
            "so the author is asked for verification evidence.\n"
            "Anchor on the findings, not intuition: deterministic and AST checks (engine="
            "DETERMINISTIC/AST_TREE_SITTER) are authoritative. If every check passed, the label is "
            "'legit' -- do not invent slop from a clean findings list. If a check "
            "failed, your label "
            "must follow from that specific failure.\n"
            'Respond with ONLY a JSON object of the form: {"label": "slop"|"needs-info"|"legit", '
            '"confidence": <number 0..1>, "reasons": [<string>, ...], '
            '"primary_evidence": <string>}. No prose outside the JSON.',
        },
        {"role": "user", "content": f"FINDINGS:\n{findings or '(none)'}"},
    ]
    state.verdict = llm(msg, schema=Verdict, role="judge")
    _apply_legit_floor(state)
    return state


def _apply_legit_floor(state: BrainState) -> None:
    """A failing deterministic finding forbids a 'legit' verdict.

    The LLM judge applies the prompt's weighing rules unevenly across runs; this
    floor makes the weakest guarantee mechanical: concrete failing evidence means
    the PR at least warrants a question to the author. It only demotes 'legit' to
    'needs-info' -- it never upgrades toward 'slop', so it cannot create a hard
    false positive.

    Only engine=DETERMINISTIC findings trigger the floor. AST findings are
    excluded because their confidence is graded against index coverage and a
    LOW-graded symbol miss is routinely an external dependency name; LLM
    findings are excluded because they are one model's reading, and weighing
    those is exactly the judgment delegated to the LLM judge.
    """
    if state.verdict is None or state.verdict.label != "legit":
        return
    det_fails = [f for f in state.findings if f.result == "fail" and f.engine == "DETERMINISTIC"]
    if not det_fails:
        return
    names = ", ".join(f.check for f in det_fails)
    state.verdict.label = "needs-info"
    state.verdict.reasons.append(
        f"deterministic floor: failing finding(s) [{names}] forbid a clean 'legit' verdict; "
        "asking the author for more information"
    )
