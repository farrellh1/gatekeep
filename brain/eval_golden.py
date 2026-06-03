from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from core.models_config import load_models_config
from core.trace import configure_logging
from eval_scoring import SLOP, aggregate, confusion, dist_str, pct, rate, run_once

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("GATEKEEP_TRACE_DIR", str(ROOT / "tests" / "golden" / "traces"))
CLONE = str(ROOT / "tests" / "fixtures" / "clone")
# only the labelled buckets are cases (skip the traces/ output dir)
CASES = sorted(
    p
    for p in glob.glob(str(ROOT / "tests" / "golden" / "*" / "*.json"))
    if pathlib.Path(p).parent.name in ("legit", "slop")
)


def _aggregate(path: str, runs: list[dict]) -> dict:
    """The golden Suite derives expected/name from the directory bucket."""
    bucket = pathlib.Path(path).parent.name
    name = f"{bucket}/{pathlib.Path(path).stem}"
    return aggregate(name, bucket == SLOP, runs)


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
        print(
            f"  {r['name']:<{w}}  {expected:<9} {vote:<10} "
            f"{agree:<6} {ok}   {dist_str(r['distribution'])}{flake}"
        )

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
    print(
        f"\n  stability:  {len(records) - len(flaky)}/{len(records)} "
        "cases gave the same answer every run"
    )
    if flaky:
        print(f"  flaky:      {', '.join(flaky)}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the LangGraph pipeline over the golden set, "
        "vote across N runs, score FPR/TPR."
    )
    ap.add_argument(
        "--runs", type=int, default=5, help="runs per case; majority vote scores (default 5)"
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=8,
        help="concurrent runs; I/O-bound, so >1 is much faster (default 8)",
    )
    ap.add_argument(
        "--max-fpr",
        type=float,
        default=0.0,
        help="fail (exit 1) if voted false-positive rate exceeds this (default 0.0)",
    )
    ap.add_argument(
        "--out",
        default=str(ROOT / "tests" / "golden" / "results.json"),
        help="path to write machine-readable results",
    )
    args = ap.parse_args()
    configure_logging()

    provider, model_name = load_models_config().resolve("default")
    if not os.environ.get(provider.api_key_env):
        print(
            f"${provider.api_key_env} not set; the golden set hits the real model.",
            file=sys.stderr,
        )
        return 2
    if not CASES:
        print("no golden cases found under tests/golden/*/*.json", file=sys.stderr)
        return 2

    model = f"{provider.name}:{model_name}"
    tasks = [(p, i) for p in CASES for i in range(args.runs)]
    by_path: dict[str, list] = {p: [None] * args.runs for p in CASES}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_once, p, CLONE, i): (p, i) for p, i in tasks}
        for fut in as_completed(futs):
            p, i = futs[fut]
            by_path[p][i] = fut.result()
    wall = time.perf_counter() - started

    records = [_aggregate(p, by_path[p]) for p in CASES]
    cm = confusion(records)
    report(records, cm, model, args.runs)
    print(f"  wall time:  {wall:.0f}s ({len(tasks)} runs, {args.workers} workers)\n")

    fpr = rate(cm["fp"], cm["fp"] + cm["tn"])
    flaky = [r["name"] for r in records if r.get("flaky")]
    artifact = {
        "model": model,
        "runs": args.runs,
        "n": len(records),
        "confusion": cm,
        "fpr": fpr,
        "tpr": rate(cm["tp"], cm["tp"] + cm["fn"]),
        "flaky_cases": flaky,
        "cases": records,
    }
    pathlib.Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"Wrote {args.out}")

    if fpr is not None and fpr > args.max_fpr:
        print(f"FAIL: voted FPR {pct(fpr)} exceeds max {pct(args.max_fpr)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
