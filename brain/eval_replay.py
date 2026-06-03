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
from core.repo_reader import _LANG_BY_EXT
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

# ADR-0003 default floors; issue 04 folds these into the master gate.
OBVIOUS, SUBTLE = "obvious", "subtle"
TPR_FLOOR = 0.60
SUBTLE_TPR_FLOOR = 0.40

# ADR-0003 sizing and quality thresholds for the go-public gate.
FPR_CEILING = 0.03  # strictly-less-than ceiling; 0.03 == ~2-3%
MIN_LEGIT_N = 150  # pooled verified-legit minimum (~150-300 in ADR-0003)
MIN_LEGIT_PER_LANGUAGE = 10  # per-language coverage floor; positive integer
MIN_SLOP_PER_STRATUM = 25  # per-stratum slop minimum (half of 50-lower bound)


def case_record(case: CorpusCase, runs: list[dict]) -> dict:
    """Fold a Corpus case's per-run outcomes into a voted record. Unlike the
    golden harness, expected/positive comes from the case `label`, and the record
    carries the `verified` flag (the Slice filter) and `language` (per-language
    coverage downstream)."""
    name = f"{case.label}/{case.source.repo}#{case.source.number}"
    rec = aggregate(name, case.label == "slop", runs)
    rec["verified"] = case.verified
    rec["language"] = case.language
    rec["slop_kind"] = case.slop_kind
    return rec


def slice_verified(records: list[dict]) -> list[dict]:
    """The Slice: only human-verified cases feed the gate. Unverified cases are
    funnel volume, reported but excluded from every rate."""
    return [r for r in records if r.get("verified")]


def tpr_strata(records: list[dict]) -> dict:
    """TPR split by Slop stratum over the passed Slice. Each stratum counts only
    verified-Slop records, skipping errors; a null slop_kind counts toward blended
    but neither stratum (the human verifier owns that judgement)."""
    buckets = {OBVIOUS: [0, 0], SUBTLE: [0, 0], "blended": [0, 0]}
    for r in records:
        if r.get("error") or not r["expected_positive"]:
            continue
        hit = r["predicted_positive"]
        buckets["blended"][0] += hit
        buckets["blended"][1] += 1
        kind = r.get("slop_kind")
        if kind in (OBVIOUS, SUBTLE):
            buckets[kind][0] += hit
            buckets[kind][1] += 1
    return {k: {"tp": tp, "n": n, "tpr": rate(tp, n)} for k, (tp, n) in buckets.items()}


def tpr_gate(
    strata: dict, tpr_floor: float = TPR_FLOOR, subtle_floor: float = SUBTLE_TPR_FLOOR
) -> dict:
    """The gate cannot pass on easy mode: a strong blended TPR is not enough if the
    subtle stratum is below floor or empty."""
    blended, subtle = strata["blended"]["tpr"], strata[SUBTLE]["tpr"]
    blended_ok = blended is not None and blended >= tpr_floor
    subtle_ok = subtle is not None and subtle >= subtle_floor
    return {
        "passed": blended_ok and subtle_ok,
        "blended_ok": blended_ok,
        "subtle_ok": subtle_ok,
        "tpr_floor": tpr_floor,
        "subtle_floor": subtle_floor,
    }


# The languages the Brain acts on in v1: the distinct tree-sitter languages the
# RepoReader can parse. ADR-0003 scopes v1 to this parseable set, so it is the
# coverage target below. A parameter rather than a literal in the scoring logic
# so the acted-on set can be narrowed or widened without editing the rate math.
ACTED_ON_LANGUAGES = frozenset(_LANG_BY_EXT.values())


def pooled_fpr(records: list[dict]) -> dict:
    """One FPR pooled across every acted-on language. The FP-driving checks
    (diff_matches_description, cosmetic_only, ci_status) are language-blind, so a
    false positive on Go is the same failure as one on Python and belongs in the
    same denominator. `n` is the named denominator: verified legit cases scored."""
    fp = 0
    n = 0
    for r in records:
        if r.get("error") or r["expected_positive"]:
            continue
        n += 1
        if r["predicted_positive"]:
            fp += 1
    return {"fp": fp, "n": n, "fpr": rate(fp, n)}


def legit_coverage(records: list[dict], acted_on: frozenset = ACTED_ON_LANGUAGES) -> dict:
    """Per-language count of verified legit cases. This is a coverage check (every
    acted-on language is exercised by at least one legit case), NOT a per-language
    FPR gate, and explicitly not a claim that tail languages are low-FP-risk: off
    the parseable set the AST net is absent. An acted-on language with zero legit
    cases is surfaced as a gap rather than omitted."""
    by_language: dict[str, int] = {}
    for r in records:
        if r.get("error") or r["expected_positive"]:
            continue
        language = r["language"]
        by_language[language] = by_language.get(language, 0) + 1
    gaps = sorted(lang for lang in acted_on if by_language.get(lang, 0) == 0)
    return {"by_language": by_language, "gaps": gaps}


def fpr_gate(pf: dict, fpr_ceiling: float = FPR_CEILING) -> dict:
    """Block if the pooled FPR is not strictly under the ceiling, or if it is None
    (no verified legit cases means there is no evidence of no harm)."""
    fpr = pf["fpr"]
    passed = fpr is not None and fpr < fpr_ceiling
    return {"passed": passed, "fpr_ceiling": fpr_ceiling}


def gate_decision(
    records: list[dict],
    acted_on: frozenset = ACTED_ON_LANGUAGES,
    tpr_floor: float = TPR_FLOOR,
    subtle_floor: float = SUBTLE_TPR_FLOOR,
    fpr_ceiling: float = FPR_CEILING,
) -> dict:
    """Master go-public gate. Composes fpr_gate, tpr_gate, and legit_coverage.
    All four sub-conditions must pass; each is exposed individually so callers can
    diagnose which dimension failed."""
    pf = pooled_fpr(records)
    fpr_result = fpr_gate(pf, fpr_ceiling=fpr_ceiling)

    strata = tpr_strata(records)
    tpr_result = tpr_gate(strata, tpr_floor=tpr_floor, subtle_floor=subtle_floor)

    coverage = legit_coverage(records, acted_on=acted_on)
    coverage_ok = len(coverage["gaps"]) == 0

    fpr_ok = fpr_result["passed"]
    blended_tpr_ok = tpr_result["blended_ok"]
    subtle_tpr_ok = tpr_result["subtle_ok"]

    passed = fpr_ok and blended_tpr_ok and subtle_tpr_ok and coverage_ok
    return {
        "passed": passed,
        "fpr_ok": fpr_ok,
        "blended_tpr_ok": blended_tpr_ok,
        "subtle_tpr_ok": subtle_tpr_ok,
        "coverage_ok": coverage_ok,
    }


def sizing_warnings(
    records: list[dict],
    acted_on: frozenset = ACTED_ON_LANGUAGES,
    min_legit_n: int = MIN_LEGIT_N,
    min_legit_per_language: int = MIN_LEGIT_PER_LANGUAGE,
    min_slop_per_stratum: int = MIN_SLOP_PER_STRATUM,
) -> list[str]:
    """Advisory warnings when the Corpus is too small to be statistically trustworthy.
    These warnings are purely informational and do NOT affect gate pass/fail."""
    warnings = []

    coverage = legit_coverage(records, acted_on=acted_on)
    pooled_legit = sum(coverage["by_language"].values())

    if pooled_legit < min_legit_n:
        warnings.append(
            f"pooled legit n={pooled_legit} is below the minimum {min_legit_n} (ADR-0003)"
        )

    for lang, count in coverage["by_language"].items():
        if count < min_legit_per_language:
            warnings.append(
                f"per-language legit n={count} for '{lang}' "
                f"is below the minimum {min_legit_per_language}"
            )

    strata = tpr_strata(records)
    for stratum_name in (OBVIOUS, SUBTLE):
        n = strata[stratum_name]["n"]
        if n < min_slop_per_stratum:
            warnings.append(
                f"{stratum_name} stratum n={n} is below the minimum {min_slop_per_stratum}"
            )

    return warnings


def report(
    records: list[dict], sl: list[dict], cm: dict, model: str, runs: int, strata: dict | None = None
) -> None:
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
            vote = r.get("vote", "—")
            ok = "✓" if r["expected_positive"] == r["predicted_positive"] else "✗"
        agreement = r.get("agreement")
        agree = f"{int(round(agreement * runs))}/{runs}" if agreement is not None else "—"
        in_slice = "slice" if r.get("verified") else "funnel"
        flake = "  FLAKY" if r.get("flaky") else ""
        distribution = r.get("distribution", {})
        print(
            f"  {r['name']:<{w}}  {expected:<9} {vote:<10} "
            f"{agree:<6} {ok}   {in_slice:<6} {dist_str(distribution)}{flake}"
        )

    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    tpr, fpr = rate(tp, tp + fn), rate(fp, fp + tn)

    print("\nBlended gate (majority vote over the Slice, positive = slop):")
    print(f"  TPR (slop caught):      {pct(tpr):>7}  ({tp}/{tp + fn})")
    print(f"  FPR (legit flagged):    {pct(fpr):>7}  ({fp}/{fp + tn})   <- Do No Harm")
    if cm["errored"]:
        print(f"  errored:                {cm['errored']}")

    pf = pooled_fpr(sl)
    print("\nPooled FPR (one rate over all acted-on languages, positive = slop):")
    print(f"  FPR (legit flagged):    {pct(pf['fpr']):>7}  ({pf['fp']}/{pf['n']} verified legit)")

    coverage = legit_coverage(sl)
    print("\nLegit coverage (verified legit n per language):")
    for language in sorted(coverage["by_language"]):
        print(f"  {language:<12} {coverage['by_language'][language]}")
    if coverage["gaps"]:
        print(f"  coverage gaps (acted-on, zero verified legit): {', '.join(coverage['gaps'])}")

    if strata is None:
        strata = tpr_strata(sl)
    gate = tpr_gate(strata)
    print("\nStratified TPR (positive = slop):")
    for k in (OBVIOUS, SUBTLE, "blended"):
        s = strata[k]
        print(f"  {k:<8} {pct(s['tpr']):>7}  ({s['tp']}/{s['n']})")
    if not gate["subtle_ok"]:
        print(f"  subtle below floor {pct(gate['subtle_floor'])} -> gate blocked")

    decision = gate_decision(sl)
    print("\nGo-public gate decision:")
    status = "passed" if decision["passed"] else "blocked"
    print(f"  master gate: {status}")
    print(
        f"  fpr_ok={decision['fpr_ok']}  "
        f"blended_tpr_ok={decision['blended_tpr_ok']}  "
        f"subtle_tpr_ok={decision['subtle_tpr_ok']}  "
        f"coverage_ok={decision['coverage_ok']}"
    )

    sw = sizing_warnings(sl)
    if sw:
        print("\nSizing warnings (advisory, do not affect gate):")
        for w in sw:
            print(f"  Warning: {w}")

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
    strata = tpr_strata(sl)
    gate = tpr_gate(strata)
    decision = gate_decision(sl)
    warnings = sizing_warnings(sl)
    report(records, sl, cm, model, args.runs, strata)
    print(f"  wall time:  {wall:.0f}s ({len(tasks)} runs, {args.workers} workers)\n")

    artifact = {
        "model": model,
        "runs": args.runs,
        "corpus_n": len(records),
        "slice_n": len(sl),
        "confusion": cm,
        "fpr": rate(cm["fp"], cm["fp"] + cm["tn"]),
        "tpr": rate(cm["tp"], cm["tp"] + cm["fn"]),
        "pooled_fpr": pooled_fpr(sl),
        "legit_coverage": legit_coverage(sl),
        "tpr_strata": strata,
        "tpr_gate": gate,
        "gate_decision": decision,
        "sizing_warnings": warnings,
        "cases": records,
    }
    pathlib.Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
