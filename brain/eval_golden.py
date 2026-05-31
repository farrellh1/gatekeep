from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import pathlib
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from core import graph
from core.llm import DEFAULT_MODEL
from core.trace import configure_logging

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("GATEKEEP_TRACE_DIR", str(ROOT / "tests" / "golden" / "traces"))
CLONE = str(ROOT / "tests" / "fixtures" / "clone")
# only the labelled buckets are cases (skip the traces/ output dir)
CASES = sorted(
    p for p in glob.glob(str(ROOT / "tests" / "golden" / "*" / "*.json"))
    if pathlib.Path(p).parent.name in ("legit", "slop")
)

SLOP = "slop"


def run_once(path: str, run_idx: int = 0) -> dict:
    """One pipeline run over a case. The delivery_id is suffixed with the run index
    so each run persists its own trace file (flaky runs aren't overwritten)."""
    case = json.load(open(path))
    event = dict(case["event"])
    event["repo"] = dict(event["repo"], clone_path=CLONE)
    event["delivery_id"] = f"{event['delivery_id']}-r{run_idx}"
    started = time.perf_counter()
    try:
        result = graph.process(event, case.get("config_yaml"))
    except Exception as exc:
        logging.getLogger("gatekeep.eval").warning("run error on %s: %s", event["delivery_id"], exc)
        return {"outcome": "error", "detail": f"{type(exc).__name__}: {exc}",
                "elapsed_s": round(time.perf_counter() - started, 2), "trace_id": event["delivery_id"]}
    elapsed = round(time.perf_counter() - started, 2)
    rec = {"elapsed_s": elapsed, "trace_id": event["delivery_id"]}
    if result.get("intake", {}).get("route") == "skip":
        rec.update(outcome="skip", confidence=None, reasons=[result.get("intake", {}).get("reason", "")])
        return rec
    verdict = result.get("verdict") or {}
    rec.update(outcome=verdict.get("label") or "?", confidence=verdict.get("confidence"),
               reasons=verdict.get("reasons", []))
    return rec


def aggregate(path: str, runs: list[dict]) -> dict:
    """Fold a case's per-run outcomes into a voted record (no LLM calls here)."""
    bucket = pathlib.Path(path).parent.name
    name = f"{bucket}/{pathlib.Path(path).stem}"
    expected_positive = bucket == SLOP

    outcomes = [r["outcome"] for r in runs]
    confs = [r["confidence"] for r in runs if r.get("confidence") is not None]
    reasons = next((r["reasons"] for r in reversed(runs) if r.get("reasons")), [])
    dist = dict(Counter(outcomes))
    base = {"name": name, "bucket": bucket, "expected_positive": expected_positive,
            "runs": len(runs), "outcomes": outcomes, "distribution": dist,
            "flaky": len(set(outcomes)) > 1,
            "elapsed_s": round(sum(r.get("elapsed_s", 0) or 0 for r in runs), 1),
            "trace_ids": [r.get("trace_id") for r in runs]}

    votable = [o for o in outcomes if o != "error"]
    if not votable:
        base.update(error="all runs errored", vote="error", label=None, predicted_positive=None, agreement=0.0)
        return base
    vote, count = Counter(votable).most_common(1)[0]
    base.update(vote=vote, label=vote, predicted_positive=vote == SLOP,
                agreement=round(count / len(runs), 3),
                confidence=round(sum(confs) / len(confs), 2) if confs else None,
                reasons=reasons)
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


def _dist_str(dist: dict) -> str:
    return ", ".join(f"{k}x{v}" for k, v in sorted(dist.items(), key=lambda kv: -kv[1]))


def report(records: list[dict], cm: dict, model: str, runs: int) -> None:
    print("\nGATEKEEP GOLDEN EVAL")
    print("=" * 72)
    print(f"{len(records)} cases x {runs} runs | model: {model}\n")

    w = max((len(r["name"]) for r in records), default=12)
    print(f"  {'case':<{w}}  {'expected':<9} {'vote':<10} {'agree':<6} ok  distribution")
    for r in records:
        expected = SLOP if r["expected_positive"] else "legit"
        if r.get("error"):
            vote, ok = "ERROR", "✗"
        else:
            vote = r["vote"]
            ok = "✓" if r["expected_positive"] == r["predicted_positive"] else "✗"
        agree = f"{int(round(r['agreement'] * runs))}/{runs}"
        flake = "  FLAKY" if r.get("flaky") else ""
        print(f"  {r['name']:<{w}}  {expected:<9} {vote:<10} {agree:<6} {ok}   {_dist_str(r['distribution'])}{flake}")

    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    tpr, fpr = rate(tp, tp + fn), rate(fp, fp + tn)
    scored = tp + fp + fn + tn
    acc = rate(tp + tn, scored)

    print("\nConfusion matrix (majority vote, positive = slop):")
    print(f"  TPR (slop caught):      {pct(tpr):>7}  ({tp}/{tp + fn})")
    print(f"  FPR (legit flagged):    {pct(fpr):>7}  ({fp}/{fp + tn})   <- Do No Harm")
    print(f"  Accuracy:               {pct(acc):>7}  ({tp + tn}/{scored})")
    if cm["errored"]:
        print(f"  errored:                {cm['errored']}")

    flaky = [r["name"] for r in records if r.get("flaky")]
    print(f"\n  stability:  {len(records) - len(flaky)}/{len(records)} cases gave the same answer every run")
    if flaky:
        print(f"  flaky:      {', '.join(flaky)}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the LangGraph pipeline over the golden set, vote across N runs, score FPR/TPR.")
    ap.add_argument("--runs", type=int, default=5, help="runs per case; majority vote scores (default 5)")
    ap.add_argument("--workers", type=int, default=8, help="concurrent runs; I/O-bound, so >1 is much faster (default 8)")
    ap.add_argument("--max-fpr", type=float, default=0.0,
                    help="fail (exit 1) if voted false-positive rate exceeds this (default 0.0)")
    ap.add_argument("--out", default=str(ROOT / "tests" / "golden" / "results.json"),
                    help="path to write machine-readable results")
    args = ap.parse_args()
    configure_logging()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set; the golden set hits the real model.", file=sys.stderr)
        return 2
    if not CASES:
        print("no golden cases found under tests/golden/*/*.json", file=sys.stderr)
        return 2

    model = os.environ.get("GATEKEEP_MODEL", DEFAULT_MODEL)
    tasks = [(p, i) for p in CASES for i in range(args.runs)]
    by_path: dict[str, list] = {p: [None] * args.runs for p in CASES}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_once, p, i): (p, i) for p, i in tasks}
        for fut in as_completed(futs):
            p, i = futs[fut]
            by_path[p][i] = fut.result()
    wall = time.perf_counter() - started

    records = [aggregate(p, by_path[p]) for p in CASES]
    cm = confusion(records)
    report(records, cm, model, args.runs)
    print(f"  wall time:  {wall:.0f}s ({len(tasks)} runs, {args.workers} workers)\n")

    fpr = rate(cm["fp"], cm["fp"] + cm["tn"])
    flaky = [r["name"] for r in records if r.get("flaky")]
    artifact = {"model": model, "runs": args.runs, "n": len(records), "confusion": cm,
                "fpr": fpr, "tpr": rate(cm["tp"], cm["tp"] + cm["fn"]),
                "flaky_cases": flaky, "cases": records}
    pathlib.Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"Wrote {args.out}")

    if fpr is not None and fpr > args.max_fpr:
        print(f"FAIL: voted FPR {pct(fpr)} exceeds max {pct(args.max_fpr)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
