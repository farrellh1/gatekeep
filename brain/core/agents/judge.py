from __future__ import annotations

from core.schemas import BrainState, Verdict
from core.llm import llm


def run(state: BrainState) -> BrainState:
    """Weigh ONLY the findings into a verdict. Does not gather, does not act.

    The Judge sees the check findings and nothing else -- given the same
    findings, the verdict is reproducible and explainable (the audit claim).
    """
    findings = "\n".join(
        f"- [{f.result}] {f.check}: {f.evidence}" for f in state.findings
    )
    msg = [
        {"role": "system", "content":
            "You are the Judge. Given ONLY these check findings, decide: label is 'slop' "
            "(clear invalidity/hallucination/no-op), 'needs-info' (likely real but missing "
            "repro/detail), or 'legit' (passes). Be conservative: prefer 'legit' or "
            "'needs-info' unless evidence of slop is strong. confidence in [0,1]. "
            "Cite the findings in reasons."},
        {"role": "user", "content": f"FINDINGS:\n{findings or '(none)'}"},
    ]
    state.verdict = llm(msg, schema=Verdict)
    return state
