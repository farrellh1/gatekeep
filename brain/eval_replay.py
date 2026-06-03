"""Score the Brain against a Corpus of real historical GitHub events.

The mirror of eval_golden with the input source swapped: it drives the same
unchanged `graph.process` seam and reuses the model-free scoring in eval_scoring,
but reads Corpus cases (ADR-0003) instead of the golden Suite and reports a
blended FPR/TPR over the human-verified Slice — the verified subset of the raw
Corpus funnel.

The stratified gate (obvious/subtle TPR floors, pooled FPR with per-language
coverage) layers on top of this skeleton in later work; here the Slice yields one
blended rate.
"""

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
from eval_corpus import CorpusCase, load_case
from eval_scoring import aggregate, confusion, dist_str, pct, rate, run_once

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
os.environ.setdefault("GATEKEEP_TRACE_DIR", str(ROOT / "tests" / "corpus" / "traces"))
# Seam 4 (RepoReader.from_index hydration) is out of scope here, so replay runs
# against the same fixture clone the golden Suite uses; the case's `snapshot`
# reference is carried but not yet hydrated.
CLONE = str(ROOT / "tests" / "fixtures" / "clone")
CORPUS_DIR = str(ROOT / "tests" / "corpus")


def case_record(case: CorpusCase, runs: list[dict]) -> dict:
    """Fold a Corpus case's per-run outcomes into a voted record. Unlike the
    golden harness, expected/positive comes from the case `label`, and the record
    carries the `verified` flag (the Slice filter) and `language` (per-language
    coverage downstream)."""
    name = f"{case.label}/{case.source.repo}#{case.source.number}"
    rec = aggregate(name, case.label == "slop", runs)
    rec["verified"] = case.verified
    rec["language"] = case.language
    return rec


def slice_verified(records: list[dict]) -> list[dict]:
    """The Slice: only human-verified cases feed the gate. Unverified cases are
    funnel volume, reported but excluded from every rate."""
    return [r for r in records if r.get("verified")]


def report(records: list[dict], sl: list[dict], cm: dict, model: str, runs: int) -> None:
    print("\nGATEKEEP REPLAY EVAL")
    print("=" * 72)
    print(
        f"Corpus: {len(records)} cases | Slice (verified): {len(sl)} cases "
        f"| {runs} runs/case | model: {model}\n"
    )

    w = max((len(r["name"]) for r in records), default=12)
    print(f"  {'case':<{w}}  {'expected':<9} {'vote':<10} {'agree':<6} ok  slice  distribution")
    for r in records:
        expected = "slop" if r["expected_positive"] else "legit"
        if r.get("error"):
            vote, ok = "ERROR", "✗"
        else:
            vote = r["vote"]
            ok = "✓" if r["expected_positive"] == r["predicted_positive"] else "✗"
        agree = f"{int(round(r['agreement'] * runs))}/{runs}"
        in_slice = "slice" if r.get("verified") else "funnel"
        flake = "  FLAKY" if r.get("flaky") else ""
        print(
            f"  {r['name']:<{w}}  {expected:<9} {vote:<10} "
            f"{agree:<6} {ok}   {in_slice:<6} {dist_str(r['distribution'])}{flake}"
        )

    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    tpr, fpr = rate(tp, tp + fn), rate(fp, fp + tn)

    print("\nBlended gate (majority vote over the Slice, positive = slop):")
    print(f"  TPR (slop caught):      {pct(tpr):>7}  ({tp}/{tp + fn})")
    print(f"  FPR (legit flagged):    {pct(fpr):>7}  ({fp}/{fp + tn})   <- Do No Harm")
    if cm["errored"]:
        print(f"  errored:                {cm['errored']}")
    print(f"\n  Slice {len(sl)} of {len(records)} Corpus cases verified\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run the LangGraph pipeline over a Corpus, vote across N runs, "
        "score a blended FPR/TPR over the verified Slice."
    )
    ap.add_argument("--runs", type=int, default=5, help="runs per case (default 5)")
    ap.add_argument("--workers", type=int, default=8, help="concurrent runs (default 8)")
    ap.add_argument("--corpus", default=CORPUS_DIR, help="Corpus directory of case JSON files")
    ap.add_argument(
        "--out",
        default=str(ROOT / "tests" / "corpus" / "results.json"),
        help="path to write machine-readable results",
    )
    args = ap.parse_args()
    configure_logging()

    provider, model_name = load_models_config().resolve("default")
    if not os.environ.get(provider.api_key_env):
        print(f"${provider.api_key_env} not set; replay hits the real model.", file=sys.stderr)
        return 2

    paths = sorted(glob.glob(os.path.join(args.corpus, "*.json")))
    cases = [load_case(p) for p in paths]
    if not cases:
        print(f"no Corpus cases found under {args.corpus}/*.json", file=sys.stderr)
        return 2

    model = f"{provider.name}:{model_name}"
    tasks = [(p, i) for p in paths for i in range(args.runs)]
    by_path: dict[str, list] = {p: [None] * args.runs for p in paths}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_once, p, CLONE, i): (p, i) for p, i in tasks}
        for fut in as_completed(futs):
            p, i = futs[fut]
            by_path[p][i] = fut.result()
    wall = time.perf_counter() - started

    records = [case_record(c, by_path[p]) for p, c in zip(paths, cases, strict=True)]
    sl = slice_verified(records)
    cm = confusion(sl)
    report(records, sl, cm, model, args.runs)
    print(f"  wall time:  {wall:.0f}s ({len(tasks)} runs, {args.workers} workers)\n")

    artifact = {
        "model": model,
        "runs": args.runs,
        "corpus_n": len(records),
        "slice_n": len(sl),
        "confusion": cm,
        "fpr": rate(cm["fp"], cm["fp"] + cm["tn"]),
        "tpr": rate(cm["tp"], cm["tp"] + cm["fn"]),
        "cases": records,
    }
    pathlib.Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
