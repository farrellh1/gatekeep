import io
import os
from pathlib import Path

import pytest

from core.models_config import load_models_config
from eval_corpus import load_corpus, parse_case
from eval_replay import (
    FPR_CEILING,
    MIN_LEGIT_N,
    MIN_LEGIT_PER_LANGUAGE,
    MIN_SLOP_PER_STRATUM,
    case_record,
    fpr_gate,
    gate_decision,
    legit_coverage,
    pooled_fpr,
    report,
    sizing_warnings,
    slice_verified,
    tpr_gate,
    tpr_strata,
)
from eval_scoring import confusion, rate, run_once

CLONE = str(Path(__file__).parent / "fixtures" / "clone")
CORPUS = Path(__file__).parent / "corpus"
_DEFAULT_PROVIDER, _ = load_models_config().resolve("default")


def _runs(*outcomes):
    return [
        {"outcome": o, "confidence": 0.9, "reasons": [], "elapsed_s": 0.1, "trace_id": "t"}
        for o in outcomes
    ]


def _case(label, verified, number, language="python"):
    signal = "rejection_label" if label == "slop" else "merged"
    return parse_case(
        {
            "label": label,
            "verified": verified,
            "slop_kind": "obvious" if label == "slop" else None,
            "language": language,
            "source": {
                "repo": "acme/widget",
                "number": number,
                "origin": "harvested",
                "signal": signal,
            },
            "snapshot": f"snapshots/{number}.json",
            "config_yaml": None,
            "event": {"delivery_id": f"c-{number}"},
        }
    )


def _rec(expected_positive, predicted_positive, slop_kind=None, language="python", error=False):
    rec = {
        "name": "x",
        "expected_positive": expected_positive,
        "predicted_positive": predicted_positive,
        "slop_kind": slop_kind,
        "language": language,
        "verified": True,
    }
    if error:
        rec["error"] = True
    return rec


def test_case_record_carries_label_verification_and_language():
    rec = case_record(_case("slop", True, 101), _runs("slop", "slop"))
    assert rec["expected_positive"] is True
    assert rec["predicted_positive"] is True
    assert rec["verified"] is True
    assert rec["language"] == "python"
    assert rec["slop_kind"] == "obvious"


def test_tpr_strata_splits_obvious_and_subtle_with_own_denominators():
    records = [
        _rec(True, True, "obvious"),
        _rec(True, False, "obvious"),
        _rec(True, True, "subtle"),
        _rec(True, True, "subtle"),
    ]
    strata = tpr_strata(records)
    assert strata["obvious"] == {"tp": 1, "n": 2, "tpr": 0.5}
    assert strata["subtle"] == {"tp": 2, "n": 2, "tpr": 1.0}


def test_tpr_gate_blocks_when_subtle_below_floor():
    records = [_rec(True, True, "obvious")] * 9 + [_rec(True, False, "subtle")]
    gate = tpr_gate(tpr_strata(records))
    assert gate["blended_ok"] is True
    assert gate["subtle_ok"] is False
    assert gate["passed"] is False


def test_tpr_gate_passes_when_both_floors_clear():
    records = [_rec(True, True, "obvious")] * 6 + [_rec(True, True, "subtle")] * 4
    gate = tpr_gate(tpr_strata(records))
    assert gate["passed"] is True


def test_tpr_gate_blocks_on_empty_subtle_stratum():
    records = [_rec(True, True, "obvious")] * 10
    gate = tpr_gate(tpr_strata(records))
    assert gate["blended_ok"] is True
    assert gate["subtle_ok"] is False
    assert gate["passed"] is False


def test_null_kind_slop_counts_blended_only():
    records = [
        _rec(True, True, "obvious"),
        _rec(True, True, "subtle"),
        _rec(True, False, None),
    ]
    strata = tpr_strata(records)
    assert strata["obvious"]["n"] == 1
    assert strata["subtle"]["n"] == 1
    assert strata["blended"]["n"] == 3
    assert strata["blended"]["tp"] == 2


def test_pooled_fpr_over_verified_legit_with_named_denominator():
    # the denominator n is the count of verified legit cases scored; slop cases
    # never enter the FPR
    records = [
        _rec(False, True),
        _rec(False, False),
        _rec(False, False),
        _rec(True, True, "obvious"),
    ]
    pf = pooled_fpr(records)
    assert pf == {"fp": 1, "n": 3, "fpr": 1 / 3}


def test_pooled_fpr_pools_tail_language_into_same_denominator():
    # a false positive on a tail language is the same language-blind failure as one
    # on Python, so it shares the single pooled denominator with no per-language split
    records = [
        _rec(False, False, language="python"),
        _rec(False, True, language="go"),
    ]
    pf = pooled_fpr(records)
    assert pf == {"fp": 1, "n": 2, "fpr": 0.5}


def test_pooled_fpr_excludes_errored_legit_from_denominator():
    # an errored legit case produced no verdict, so it cannot count toward n
    records = [
        _rec(False, False),
        _rec(False, False, error=True),
    ]
    pf = pooled_fpr(records)
    assert pf == {"fp": 0, "n": 1, "fpr": 0.0}


def test_legit_coverage_reports_per_language_n():
    records = [
        _rec(False, False, language="python"),
        _rec(False, True, language="python"),
        _rec(False, False, language="go"),
        _rec(True, True, "obvious", language="rust"),  # slop, excluded from coverage
    ]
    coverage = legit_coverage(records)
    assert coverage["by_language"] == {"python": 2, "go": 1}


def test_legit_coverage_surfaces_acted_on_language_with_zero_legit_as_gap():
    # an acted-on language exercised by no verified legit case is a coverage gap,
    # not silently omitted
    records = [_rec(False, False, language="python")]
    coverage = legit_coverage(records, acted_on=frozenset({"python", "go"}))
    assert coverage["gaps"] == ["go"]
    assert "python" not in coverage["gaps"]


def test_legit_coverage_acted_on_set_is_overridable():
    # the acted-on set is a parameter, so gaps reflect exactly the set passed in
    records = [_rec(False, False, language="python")]
    coverage = legit_coverage(records, acted_on=frozenset({"python", "ruby", "java"}))
    assert coverage["gaps"] == ["java", "ruby"]


def test_legit_case_record_is_negative():
    rec = case_record(_case("legit", True, 201), _runs("legit"))
    assert rec["expected_positive"] is False


def test_slice_keeps_only_verified_cases():
    records = [
        case_record(_case("legit", True, 201), _runs("legit")),
        case_record(_case("slop", True, 101), _runs("slop")),
        case_record(_case("legit", False, 202), _runs("legit")),  # funnel only
    ]
    sl = slice_verified(records)
    assert len(sl) == 2
    assert all(r["verified"] for r in sl)


@pytest.mark.golden
@pytest.mark.skipif(
    not os.environ.get(_DEFAULT_PROVIDER.api_key_env),
    reason=f"replay drives the real model; set ${_DEFAULT_PROVIDER.api_key_env} to run",
)
def test_replay_drives_graph_and_scores_only_the_slice(tmp_path):
    # the harness must reach a real blended rate over the verified Slice while the
    # unverified case stays funnel-only
    # load_corpus returns cases in sorted-path order, so they align with paths
    cases = load_corpus(CORPUS)
    paths = sorted(str(p) for p in CORPUS.glob("*.json"))
    records = []
    for path, case in zip(paths, cases, strict=True):
        run = run_once(path, CLONE, 0)
        records.append(case_record(case, [run]))

    sl = slice_verified(records)
    assert len(records) == 3
    assert len(sl) == 2  # one unverified case excluded from the gate
    cm = confusion(sl)
    fpr = rate(cm["fp"], cm["fp"] + cm["tn"])
    assert fpr == 0.0, f"legit Slice case flagged as slop: {cm}"


# ---------------------------------------------------------------------------
# Module-level threshold constants (ADR-0003 defaults)
# ---------------------------------------------------------------------------


def test_module_constants_have_adr_0003_defaults():
    # ADR-0003: FPR hard gate <~2-3%; default ceiling 0.03
    assert FPR_CEILING == 0.03
    # ADR-0003 sizing: ~150-300 hand-verified legit cases
    assert MIN_LEGIT_N == 150
    # sensible per-language coverage floor; must be a positive integer
    assert isinstance(MIN_LEGIT_PER_LANGUAGE, int) and MIN_LEGIT_PER_LANGUAGE > 0
    # ADR-0003 sizing: ~50-100 slop cases split obvious/subtle; 25 per stratum
    assert MIN_SLOP_PER_STRATUM == 25


# ---------------------------------------------------------------------------
# fpr_gate — a pure function: FPR strictly under the ceiling
# ---------------------------------------------------------------------------


def test_fpr_gate_passes_when_fpr_strictly_under_ceiling():
    # pooled FPR 0/200 is comfortably below the 3% ceiling
    pf = {"fp": 0, "n": 200, "fpr": 0.0}
    result = fpr_gate(pf)
    assert result["passed"] is True


def test_fpr_gate_blocks_when_fpr_at_ceiling():
    # FPR == ceiling is NOT strictly under; the gate must block
    pf = {"fp": 3, "n": 100, "fpr": 0.03}
    result = fpr_gate(pf, fpr_ceiling=0.03)
    assert result["passed"] is False


def test_fpr_gate_blocks_when_fpr_above_ceiling():
    pf = {"fp": 5, "n": 100, "fpr": 0.05}
    result = fpr_gate(pf, fpr_ceiling=0.03)
    assert result["passed"] is False


def test_fpr_gate_blocks_when_fpr_is_none():
    # FPR is None when there are zero legit cases; no evidence of no harm means the gate blocks.
    # An empty legit Slice must NOT pass (ADR-0003: the denominator must be named).
    pf = {"fp": 0, "n": 0, "fpr": None}
    result = fpr_gate(pf)
    assert result["passed"] is False


def test_fpr_gate_result_names_its_ceiling():
    # the result dict carries the ceiling used so callers can log/report it
    pf = {"fp": 0, "n": 10, "fpr": 0.0}
    result = fpr_gate(pf, fpr_ceiling=0.05)
    assert result["fpr_ceiling"] == 0.05


def test_fpr_gate_ceiling_is_a_parameter_that_flips_decision():
    # a tight ceiling (0.01) blocks an FPR of 2%; relaxing to 0.05 passes it
    pf = {"fp": 2, "n": 100, "fpr": 0.02}
    assert fpr_gate(pf, fpr_ceiling=0.01)["passed"] is False
    assert fpr_gate(pf, fpr_ceiling=0.05)["passed"] is True


# ---------------------------------------------------------------------------
# gate_decision — master gate combining FPR, TPR, subtle TPR, coverage
# ---------------------------------------------------------------------------


def _passing_records(legit_languages=("python", "go")):
    """Fabricate a minimal record set that clears all four gate conditions.

    - pooled FPR: 0 FPs over enough legit cases (well under 3% ceiling)
    - blended TPR: 9/10 obvious + 5/6 subtle  -> 14/16 = 0.875  >= 0.60
    - subtle TPR: 5/6 = 0.83  >= 0.40
    - coverage: all legit_languages present
    """
    records = []
    # legit cases — one per language, no false positives
    for lang in legit_languages:
        records.append(_rec(False, False, language=lang))
    # obvious slop caught
    for _ in range(9):
        records.append(_rec(True, True, "obvious", language="python"))
    records.append(_rec(True, False, "obvious", language="python"))
    # subtle slop caught
    for _ in range(5):
        records.append(_rec(True, True, "subtle", language="python"))
    records.append(_rec(True, False, "subtle", language="python"))
    return records


def test_gate_decision_passes_when_all_conditions_met():
    records = _passing_records(legit_languages=["python", "go"])
    decision = gate_decision(records, acted_on=frozenset({"python", "go"}))
    assert decision["passed"] is True


def test_gate_decision_exposes_sub_condition_booleans():
    # the result dict must contain individual sub-condition keys so callers can
    # diagnose which dimension failed without re-deriving the logic
    records = _passing_records(legit_languages=["python", "go"])
    decision = gate_decision(records, acted_on=frozenset({"python", "go"}))
    assert "fpr_ok" in decision
    assert "blended_tpr_ok" in decision
    assert "subtle_tpr_ok" in decision
    assert "coverage_ok" in decision
    assert "passed" in decision


def test_gate_decision_blocks_when_fpr_fails_alone():
    # FPR above ceiling is the only failing dimension; everything else passes
    records = _passing_records(legit_languages=["python", "go"])
    # inject enough false positives to push FPR above 3%
    for _ in range(10):
        records.append(_rec(False, True, language="python"))
    decision = gate_decision(records, acted_on=frozenset({"python", "go"}), fpr_ceiling=0.03)
    assert decision["fpr_ok"] is False
    assert decision["passed"] is False


def test_gate_decision_blocks_when_blended_tpr_fails_alone():
    # only obvious cases, none caught -> blended TPR = 0
    records = [_rec(False, False, language="python")] * 50
    records += [_rec(True, False, "obvious", language="python")] * 10
    records += [_rec(True, True, "subtle", language="python")] * 5
    decision = gate_decision(records, acted_on=frozenset({"python"}), tpr_floor=0.60)
    assert decision["blended_tpr_ok"] is False
    assert decision["passed"] is False


def test_gate_decision_blocks_when_subtle_tpr_fails_alone():
    # blended TPR passes but subtle stratum is below its floor
    records = [_rec(False, False, language="python")] * 50
    # lots of obvious caught: blended TPR high
    records += [_rec(True, True, "obvious", language="python")] * 20
    # subtle not caught
    records += [_rec(True, False, "subtle", language="python")] * 5
    decision = gate_decision(records, acted_on=frozenset({"python"}), subtle_floor=0.40)
    assert decision["subtle_tpr_ok"] is False
    assert decision["passed"] is False


def test_gate_decision_blocks_when_coverage_gap_exists():
    # acted_on includes "go" but the Slice has no legit go case -> coverage gap -> blocked
    records = _passing_records(legit_languages=["python"])
    decision = gate_decision(records, acted_on=frozenset({"python", "go"}))
    assert decision["coverage_ok"] is False
    assert decision["passed"] is False


def test_gate_decision_zero_legit_cases_blocks_via_coverage_and_fpr():
    # a Slice with no legit cases at all fails both FPR (None) and coverage checks
    records = [_rec(True, True, "obvious")] * 10 + [_rec(True, True, "subtle")] * 5
    decision = gate_decision(records, acted_on=frozenset({"python"}))
    assert decision["passed"] is False


def test_gate_decision_fpr_ceiling_parameter_flips_decision():
    # the default 3% ceiling blocks; relaxing to 20% passes the same records
    records = _passing_records(legit_languages=["python", "go"])
    # one FP among twelve legit cases is an ~8% FPR: above the 3% ceiling, below
    # the relaxed 20% one, so the ceiling parameter alone flips the decision
    records += [_rec(False, False, language="python")] * 9
    records_with_fp = records + [_rec(False, True, language="python")]
    decision_tight = gate_decision(
        records_with_fp, acted_on=frozenset({"python", "go"}), fpr_ceiling=0.03
    )
    decision_loose = gate_decision(
        records_with_fp, acted_on=frozenset({"python", "go"}), fpr_ceiling=0.20
    )
    assert decision_tight["passed"] is False
    assert decision_loose["passed"] is True


def test_gate_decision_tpr_floor_parameter_flips_decision():
    # blended TPR of 0.65 passes a 0.60 floor but fails a 0.70 floor
    records = [_rec(False, False, language="python")] * 50
    # 13 caught out of 20 = 0.65
    records += [_rec(True, True, "obvious", language="python")] * 9
    records += [_rec(True, False, "obvious", language="python")] * 2
    records += [_rec(True, True, "subtle", language="python")] * 4
    records += [_rec(True, False, "subtle", language="python")] * 5
    decision_low = gate_decision(
        records, acted_on=frozenset({"python"}), tpr_floor=0.60, subtle_floor=0.30
    )
    decision_high = gate_decision(
        records, acted_on=frozenset({"python"}), tpr_floor=0.70, subtle_floor=0.30
    )
    assert decision_low["passed"] is True
    assert decision_high["passed"] is False


def test_gate_decision_composes_tpr_gate_not_duplicates_it():
    # gate_decision must delegate to tpr_gate rather than re-implement the logic;
    # we verify the sub-condition booleans match what tpr_gate would return
    records = _passing_records(legit_languages=["python", "go"])
    strata = tpr_strata(records)
    tpr_result = tpr_gate(strata)
    decision = gate_decision(records, acted_on=frozenset({"python", "go"}))
    assert decision["blended_tpr_ok"] == tpr_result["blended_ok"]
    assert decision["subtle_tpr_ok"] == tpr_result["subtle_ok"]


# ---------------------------------------------------------------------------
# sizing_warnings — separate signal; must NOT affect gate pass/fail
# ---------------------------------------------------------------------------


def test_sizing_warnings_is_empty_for_well_sized_slice():
    # a legit Slice above all thresholds and strata above min n must produce no warnings
    records = []
    # 200 legit cases: 100 python, 100 go
    for lang in ("python", "go"):
        for _ in range(100):
            records.append(_rec(False, False, language=lang))
    # 30 obvious slop + 30 subtle slop (both above min_slop_per_stratum=25)
    for _ in range(30):
        records.append(_rec(True, True, "obvious", language="python"))
    for _ in range(30):
        records.append(_rec(True, True, "subtle", language="python"))
    warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python", "go"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert warnings == []


def test_sizing_warnings_when_pooled_legit_below_minimum():
    # ADR-0003: ~150-300 verified legit cases; fewer than 150 triggers a warning
    records = [_rec(False, False, language="python")] * 50
    records += [_rec(True, True, "obvious", language="python")] * 30
    records += [_rec(True, True, "subtle", language="python")] * 30
    warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert any("legit" in w.lower() for w in warnings)
    assert len(warnings) >= 1


def test_sizing_warnings_when_per_language_legit_below_minimum():
    # a language with fewer than min_legit_per_language verified legit cases triggers a warning
    records = [_rec(False, False, language="python")] * 200
    records += [_rec(False, False, language="go")] * 3  # below the floor
    records += [_rec(True, True, "obvious", language="python")] * 30
    records += [_rec(True, True, "subtle", language="python")] * 30
    warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python", "go"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert any("go" in w.lower() for w in warnings)


def test_sizing_warnings_when_obvious_stratum_below_minimum():
    # ADR-0003: ~50-100 slop split obvious/subtle; below 25 per stratum triggers a warning
    records = [_rec(False, False, language="python")] * 200
    records += [_rec(True, True, "obvious", language="python")] * 10  # below 25
    records += [_rec(True, True, "subtle", language="python")] * 30
    warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert any("obvious" in w.lower() for w in warnings)


def test_sizing_warnings_when_subtle_stratum_below_minimum():
    records = [_rec(False, False, language="python")] * 200
    records += [_rec(True, True, "obvious", language="python")] * 30
    records += [_rec(True, True, "subtle", language="python")] * 5  # below 25
    warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert any("subtle" in w.lower() for w in warnings)


def test_sizing_warnings_do_not_affect_gate_pass_fail():
    # a Slice that is too small to be trusted (triggers warnings) can still pass or
    # fail the gate purely on rates; warnings are a separate signal
    # here: 10 legit cases (below 150 floor) but 0 FPs, good TPR, no coverage gap
    records = [_rec(False, False, language="python")] * 10
    records += [_rec(True, True, "obvious", language="python")] * 9
    records += [_rec(True, True, "subtle", language="python")] * 5
    warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    decision = gate_decision(records, acted_on=frozenset({"python"}))
    # warnings exist (legit n too small) but gate is decided independently
    assert len(warnings) >= 1
    # the gate result is purely rate-based — it does NOT read the warnings list
    assert "passed" in decision
    # the gate must NOT block solely because of the under-n warning
    assert decision["passed"] is True


def test_sizing_warnings_threshold_parameters_are_overridable():
    # min_legit_n is a parameter; raising it above actual n flips a no-warning to a warning
    records = [_rec(False, False, language="python")] * 200
    records += [_rec(True, True, "obvious", language="python")] * 30
    records += [_rec(True, True, "subtle", language="python")] * 30
    no_warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python"}),
        min_legit_n=150,
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert no_warnings == []
    with_warnings = sizing_warnings(
        records,
        acted_on=frozenset({"python"}),
        min_legit_n=500,  # raised above actual 200
        min_legit_per_language=10,
        min_slop_per_stratum=25,
    )
    assert any("legit" in w.lower() for w in with_warnings)


# ---------------------------------------------------------------------------
# report() — denominators named, warnings visible and labelled
# ---------------------------------------------------------------------------


def _report_output(records, corpus_records=None) -> str:
    """Capture report() stdout into a string for assertion."""
    if corpus_records is None:
        corpus_records = records
    from eval_replay import tpr_strata
    from eval_scoring import confusion

    sl = records
    cm = confusion(sl)
    buf = io.StringIO()
    import sys

    old, sys.stdout = sys.stdout, buf
    try:
        report(corpus_records, sl, cm, model="test:model", runs=1, strata=tpr_strata(sl))
    finally:
        sys.stdout = old
    return buf.getvalue()


def test_report_names_pooled_legit_n():
    # report must state the pooled legit denominator explicitly so the number is
    # not hidden behind a rate; a reader should see "N verified legit" in the output
    records = [_rec(False, False, language="python")] * 5
    records += [_rec(True, True, "obvious")] * 3
    records += [_rec(True, True, "subtle")] * 2
    output = _report_output(records)
    # the pooled legit n must appear alongside the FPR
    assert "5" in output  # 5 legit cases is the denominator


def test_report_names_per_stratum_slop_n():
    # report must state per-stratum Slop n (obvious n and subtle n) so a reader
    # can judge whether the stratified TPR denominators are large enough
    records = [_rec(False, False, language="python")] * 5
    records += [_rec(True, True, "obvious")] * 4
    records += [_rec(True, True, "subtle")] * 3
    output = _report_output(records)
    # obvious and subtle counts must appear
    assert "obvious" in output
    assert "subtle" in output
    assert "4" in output  # obvious n
    assert "3" in output  # subtle n


def test_report_names_per_language_legit_n():
    # the per-language legit count must be visible in the report so a gap is not
    # hidden behind a healthy pooled number
    records = [
        _rec(False, False, language="python"),
        _rec(False, False, language="python"),
        _rec(False, False, language="go"),
    ]
    records += [_rec(True, True, "obvious")] * 3
    records += [_rec(True, True, "subtle")] * 3
    output = _report_output(records)
    assert "python" in output
    assert "go" in output


def test_report_names_slice_vs_corpus_size():
    # report must distinguish Slice n from raw Corpus n so the funnel ratio is visible
    corpus = [
        _rec(False, False, language="python"),
        _rec(False, False, language="python"),
    ]
    # second record is funnel-only (not verified) — simulate by using corpus_records != sl
    sl = corpus[:1]
    from eval_replay import tpr_strata
    from eval_scoring import confusion

    cm = confusion(sl)
    buf = io.StringIO()
    import sys

    old, sys.stdout = sys.stdout, buf
    try:
        report(corpus, sl, cm, model="test:model", runs=1, strata=tpr_strata(sl))
    finally:
        sys.stdout = old
    output = buf.getvalue()
    # both counts must appear: Slice n and Corpus n
    assert "1" in output  # Slice n
    assert "2" in output  # Corpus n


def test_report_prints_sizing_warnings_labelled_as_warnings():
    # sizing warnings must be printed and must be clearly labelled as warnings (not
    # as gate failures), so a reader knows the gate may still pass
    records = [_rec(False, False, language="python")] * 10  # below 150 floor
    records += [_rec(True, True, "obvious")] * 3
    records += [_rec(True, True, "subtle")] * 3
    output = _report_output(records)
    # the word "warning" (case-insensitive) must appear in the output
    assert "warning" in output.lower()


def test_report_prints_gate_decision():
    # the master gate decision (passed/blocked) must appear in the report output
    records = [_rec(False, False, language="python")] * 50
    records += [_rec(True, True, "obvious")] * 10
    records += [_rec(True, True, "subtle")] * 6
    output = _report_output(records)
    # either "passed" or "blocked" (or both, in the context of the gate section)
    assert "passed" in output.lower() or "blocked" in output.lower()


def test_report_warning_is_distinct_from_gate_blocked_line():
    # warnings and gate-blocked lines must use different phrasing so a reader can
    # distinguish an advisory (warning) from a hard gate failure
    records = [_rec(False, False, language="python")] * 10  # triggers under-n warning
    records += [_rec(True, True, "obvious")] * 3
    records += [_rec(True, True, "subtle")] * 3
    output = _report_output(records)
    lines = output.splitlines()
    warning_lines = [line for line in lines if "warning" in line.lower()]
    # warnings must exist and none of them must also claim the gate is blocked
    assert len(warning_lines) >= 1
    for wline in warning_lines:
        assert "gate blocked" not in wline.lower()
