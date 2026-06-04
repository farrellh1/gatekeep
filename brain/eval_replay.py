"""Score the Brain against a Corpus of real historical GitHub events.

The mirror of eval_golden with the input source swapped: it drives the same
unchanged `graph.process` seam and reuses the model-free scoring in eval_scoring,
but reads Corpus cases instead of the golden Suite and scores a stratified gate
over the human-verified Slice — the verified subset of the raw Corpus funnel.

The master gate (gate_decision) folds four dimensions into one go/no-go: pooled
FPR under a hard ceiling, blended TPR above its floor, subtle-stratum TPR above
its lower floor, and no acted-on language left as a coverage gap. Thresholds and
minimum-n sizing targets are parameters with defaults, so they can be tightened
without touching the rate math. A tier below its minimum n warns (power_warnings)
without failing the gate, so an under-powered Slice is visible rather than a
silent pass.
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
# Replay is backed by this one fixture clone; a case's `snapshot` reference is
# carried through but unused, since the RepoReader reads the clone rather than
# hydrating from the frozen index.
CLONE = str(ROOT / "tests" / "fixtures" / "clone")
CORPUS_DIR = str(ROOT / "tests" / "corpus")

# Gate thresholds: the floors and ceiling are the pass/fail bounds, read as
# parameters by the master gate so they can be tightened without editing the rate
# math.
OBVIOUS, SUBTLE = "obvious", "subtle"
TPR_FLOOR = 0.60
SUBTLE_TPR_FLOOR = 0.40
# A false positive on a real contribution is the product's defining failure, so
# FPR is the hard gate; its bound is the strict end of the ~2–3% go-public ceiling.
FPR_CEILING = 0.02

# Sizing targets. A tier below its minimum n does not fail the gate; it warns, so
# an under-powered Slice is visible rather than passing silently on too few cases:
# ~150–300 pooled legit, a per-language allotment for coverage, and ~50–100 Slop
# split obvious/subtle (~25 per stratum).
MIN_POOLED_LEGIT_N = 150
MIN_PER_LANGUAGE_N = 10
MIN_SLOP_STRATUM_N = 25


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


# The languages the Brain acts on: the distinct tree-sitter languages the
# RepoReader can parse, which is the coverage target below. A parameter rather
# than a literal in the scoring logic so the acted-on set can be narrowed or
# widened without editing the rate math.
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


def fpr_gate(pooled: dict, ceiling: float = FPR_CEILING) -> dict:
    """The hard gate: pooled FPR must sit at or below the ceiling. An unmeasured
    FPR (no verified legit cases, so fpr is None) cannot pass — the do-no-harm
    claim needs evidence, and absence of evidence is not a pass."""
    fpr = pooled["fpr"]
    passed = fpr is not None and fpr <= ceiling
    return {"passed": passed, "fpr": fpr, "ceiling": ceiling}


def coverage_gate(coverage: dict) -> dict:
    """An acted-on language with zero verified legit cases blocks the pass: the
    do-no-harm claim cannot silently extend to a language the Slice never
    exercised, and the tail is where the AST net is absent."""
    gaps = coverage["gaps"]
    return {"passed": not gaps, "gaps": gaps}


def gate_decision(
    records: list[dict],
    acted_on: frozenset = ACTED_ON_LANGUAGES,
    *,
    fpr_ceiling: float = FPR_CEILING,
    tpr_floor: float = TPR_FLOOR,
    subtle_floor: float = SUBTLE_TPR_FLOOR,
) -> dict:
    """The master go/no-go gate over a Slice. A pass requires every dimension:
    pooled FPR under the ceiling, blended TPR above its floor, subtle-stratum TPR
    above its (lower) floor, and no acted-on language left as a coverage gap;
    failing any one fails the gate. The pieces it computed are carried in the
    result so the report renders the exact numbers the decision was made on."""
    pooled = pooled_fpr(records)
    coverage = legit_coverage(records, acted_on)
    strata = tpr_strata(records)
    fg = fpr_gate(pooled, fpr_ceiling)
    tg = tpr_gate(strata, tpr_floor, subtle_floor)
    cg = coverage_gate(coverage)
    return {
        "passed": fg["passed"] and tg["passed"] and cg["passed"],
        "fpr_gate": fg,
        "tpr_gate": tg,
        "coverage_gate": cg,
        "pooled_fpr": pooled,
        "tpr_strata": strata,
        "legit_coverage": coverage,
    }


def power_warnings(
    pooled: dict,
    coverage: dict,
    strata: dict,
    acted_on: frozenset = ACTED_ON_LANGUAGES,
    *,
    min_pooled_legit: int = MIN_POOLED_LEGIT_N,
    min_per_language: int = MIN_PER_LANGUAGE_N,
    min_slop_stratum: int = MIN_SLOP_STRATUM_N,
) -> list[dict]:
    """Tiers whose n sits below its sizing target. A warning is distinct
    from a gate failure: it does not block the pass, it marks a rate as
    under-powered so the report never reads a confident number off too few cases.
    A language at zero legit is a coverage gap (a gate failure), not a warning, so
    only nonzero-but-thin per-language tiers warn here."""
    warnings = []
    if pooled["n"] < min_pooled_legit:
        warnings.append({"tier": "pooled legit", "n": pooled["n"], "min": min_pooled_legit})
    for language in sorted(acted_on):
        have = coverage["by_language"].get(language, 0)
        if 0 < have < min_per_language:
            warnings.append({"tier": f"legit:{language}", "n": have, "min": min_per_language})
    for stratum in (OBVIOUS, SUBTLE):
        n = strata[stratum]["n"]
        if n < min_slop_stratum:
            warnings.append({"tier": f"slop:{stratum}", "n": n, "min": min_slop_stratum})
    return warnings


def _ok(passed: bool) -> str:
    return "pass" if passed else "FAIL"


def report(
    records: list[dict],
    sl: list[dict],
    cm: dict,
    gate: dict,
    warnings: list[dict],
    model: str,
    runs: int,
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
            vote = r["vote"]
            ok = "✓" if r["expected_positive"] == r["predicted_positive"] else "✗"
        agree = f"{int(round(r['agreement'] * runs))}/{runs}"
        in_slice = "slice" if r.get("verified") else "funnel"
        flake = "  FLAKY" if r.get("flaky") else ""
        print(
            f"  {r['name']:<{w}}  {expected:<9} {vote:<10} "
            f"{agree:<6} {ok}   {in_slice:<6} {dist_str(r['distribution'])}{flake}"
        )

    pooled = gate["pooled_fpr"]
    strata = gate["tpr_strata"]
    coverage = gate["legit_coverage"]
    fg, tg, cg = gate["fpr_gate"], gate["tpr_gate"], gate["coverage_gate"]

    # The go/no-go headline. Each line is one dimension of the master gate, shown
    # with the rate and the denominator it was computed over.
    print(f"\nGO-PUBLIC GATE: {'GO' if gate['passed'] else 'NO-GO'}")
    print(
        f"  pooled FPR <= {pct(fg['ceiling']):<6} {_ok(fg['passed']):<5} "
        f"{pct(fg['fpr'])} over {pooled['n']} verified legit   <- Do No Harm"
    )
    print(
        f"  blended TPR >= {pct(tg['tpr_floor']):<5} {_ok(tg['blended_ok']):<5} "
        f"{pct(strata['blended']['tpr'])} over {strata['blended']['n']} slop"
    )
    print(
        f"  subtle TPR >= {pct(tg['subtle_floor']):<6} {_ok(tg['subtle_ok']):<5} "
        f"{pct(strata['subtle']['tpr'])} over {strata['subtle']['n']} subtle slop"
    )
    gaps = f"  gaps: {', '.join(cg['gaps'])}" if cg["gaps"] else ""
    print(f"  no coverage gaps      {_ok(cg['passed']):<5}{gaps}")
    if cm["errored"]:
        print(f"  errored cases:        {cm['errored']}")

    # Every denominator is named below so an under-powered slice or an unexercised
    # language is visible rather than hidden behind a headline rate.
    print("\nPooled FPR (one rate over all acted-on languages, positive = slop):")
    denom = f"{pooled['fp']}/{pooled['n']} verified legit"
    print(f"  FPR (legit flagged):    {pct(pooled['fpr']):>7}  ({denom})")

    print("\nStratified TPR (positive = slop):")
    for k in (OBVIOUS, SUBTLE, "blended"):
        s = strata[k]
        print(f"  {k:<8} {pct(s['tpr']):>7}  ({s['tp']}/{s['n']})")

    print("\nLegit coverage (verified legit n per language):")
    for language in sorted(coverage["by_language"]):
        print(f"  {language:<12} {coverage['by_language'][language]}")
    if coverage["gaps"]:
        print(f"  coverage gaps (acted-on, zero verified legit): {', '.join(coverage['gaps'])}")

    # Under-powered tiers are a warning, never a silent pass and never a gate
    # failure: the gate can still read GO while a rate rests on too few cases.
    if warnings:
        print("\nUnder-powered tiers (warning, not a gate failure):")
        for warn in warnings:
            print(f"  {warn['tier']:<16} n={warn['n']} below min {warn['min']}")
    else:
        print("\nAll tiers meet their minimum n.")

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
    gate = gate_decision(sl)
    warnings = power_warnings(gate["pooled_fpr"], gate["legit_coverage"], gate["tpr_strata"])
    report(records, sl, cm, gate, warnings, model, args.runs)
    print(f"  wall time:  {wall:.0f}s ({len(tasks)} runs, {args.workers} workers)\n")

    artifact = {
        "model": model,
        "runs": args.runs,
        "corpus_n": len(records),
        "slice_n": len(sl),
        "confusion": cm,
        "gate": gate,
        "warnings": warnings,
        "cases": records,
    }
    pathlib.Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
