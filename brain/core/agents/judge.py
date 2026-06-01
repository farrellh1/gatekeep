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
            "HIGH-confidence fails carry full weight. In particular, a HIGH-confidence "
            "cited_symbols_exist fail means the referenced code genuinely does not exist anywhere "
            "in the repo (the index covers references, not just definitions, so a real but "
            "imported/used symbol would pass) -- treat this as a hallucinated reference and label "
            "'slop', for issues and PRs alike. Do not soften it to 'needs-info' on the theory it "
            "might be a missing dependency or stale name; a HIGH-confidence miss has "
            "ruled that out.\n"
            "Do No Harm applies to weak evidence: a 'fail' with LOW confidence (engine=HEURISTIC) "
            "comes from a degraded language path we could not fully parse -- it MAY be a coverage "
            "gap, not real slop. A LOW-confidence fail MUST NOT by itself justify a 'slop' label; "
            "treat it as at most 'needs-info' unless an independent HIGH-confidence "
            "finding confirms "
            "the problem.\n"
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
    return state
