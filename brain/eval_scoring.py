"""Model-free scoring shared by eval_golden and eval_replay.

`run_once` drives the Brain through the unchanged `graph.process` seam; everything
below it (`aggregate` voting, `confusion`, `rate`) folds run outcomes into rates
without an LLM call, so both harnesses score on one path.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter

from core import graph

SLOP = "slop"


def run_once(path: str, clone_path: str, run_idx: int = 0) -> dict:
    """One pipeline run over a case. The clone is injected by the caller so the
    same driver serves the golden Suite and a Corpus case. The delivery_id is
    suffixed with the run index so each run persists its own trace file (flaky
    runs aren't overwritten)."""
    with open(path) as f:
        case = json.load(f)
    event = dict(case["event"])
    event["repo"] = dict(event["repo"], clone_path=clone_path)
    event["delivery_id"] = f"{event['delivery_id']}-r{run_idx}"
    started = time.perf_counter()
    try:
        result = graph.process(event, case.get("config_yaml"))
    except Exception as exc:
        logging.getLogger("gatekeep.eval").warning("run error on %s: %s", event["delivery_id"], exc)
        return {
            "outcome": "error",
            "detail": f"{type(exc).__name__}: {exc}",
            "elapsed_s": round(time.perf_counter() - started, 2),
            "trace_id": event["delivery_id"],
        }
    elapsed = round(time.perf_counter() - started, 2)
    rec = {"elapsed_s": elapsed, "trace_id": event["delivery_id"]}
    if result.get("intake", {}).get("route") == "skip":
        rec.update(
            outcome="skip", confidence=None, reasons=[result.get("intake", {}).get("reason", "")]
        )
        return rec
    verdict = result.get("verdict") or {}
    rec.update(
        outcome=verdict.get("label") or "?",
        confidence=verdict.get("confidence"),
        reasons=verdict.get("reasons", []),
    )
    return rec


def aggregate(name: str, expected_positive: bool, runs: list[dict]) -> dict:
    """Fold a case's per-run outcomes into a voted record (no LLM calls here).

    `name` and `expected_positive` come from the caller — the golden harness
    derives them from the directory bucket, the replay harness from the case
    label — so the voting logic stays the same across both."""
    outcomes = [r["outcome"] for r in runs]
    confs = [r["confidence"] for r in runs if r.get("confidence") is not None]
    reasons = next((r["reasons"] for r in reversed(runs) if r.get("reasons")), [])
    dist = dict(Counter(outcomes))
    base = {
        "name": name,
        "expected_positive": expected_positive,
        "runs": len(runs),
        "outcomes": outcomes,
        "distribution": dist,
        "flaky": len(set(outcomes)) > 1,
        "elapsed_s": round(sum(r.get("elapsed_s", 0) or 0 for r in runs), 1),
        "trace_ids": [r.get("trace_id") for r in runs],
    }

    votable = [o for o in outcomes if o != "error"]
    if not votable:
        base.update(
            error="all runs errored",
            vote="error",
            label=None,
            predicted_positive=None,
            agreement=0.0,
        )
        return base
    vote, count = Counter(votable).most_common(1)[0]
    base.update(
        vote=vote,
        label=vote,
        predicted_positive=vote == SLOP,
        agreement=round(count / len(runs), 3),
        confidence=round(sum(confs) / len(confs), 2) if confs else None,
        reasons=reasons,
    )
    return base


def confusion(records: list[dict]) -> dict:
    tp = fp = fn = tn = errored = 0
    for r in records:
        if r.get("error"):
            errored += 1
            continue
        actual, pred = r["expected_positive"], r["predicted_positive"]
        if actual and pred:
            tp += 1
        elif actual and not pred:
            fn += 1
        elif not actual and pred:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "errored": errored}


def rate(num: int, den: int) -> float | None:
    return num / den if den else None


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def dist_str(dist: dict) -> str:
    return ", ".join(f"{k}x{v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1]))
