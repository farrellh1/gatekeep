import os
from pathlib import Path

import pytest

from core.models_config import load_models_config
from eval_corpus import load_corpus, parse_case
from eval_replay import (
    case_record,
    coverage_gate,
    fpr_gate,
    gate_decision,
    legit_coverage,
    pooled_fpr,
    power_warnings,
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


def _rec(
    expected_positive,
    predicted_positive,
    slop_kind=None,
    language="python",
    error=False,
    predicted_flagged=None,
):
    # A condemned case is always also flagged; default flagged to the condemn value
    # so a plain _rec models pass/condemn. Pass predicted_flagged=True with
    # predicted_positive=False to model a needs-info verdict.
    rec = {
        "name": "x",
        "expected_positive": expected_positive,
        "predicted_positive": predicted_positive,
        "predicted_flagged": predicted_positive if predicted_flagged is None else predicted_flagged,
        "slop_kind": slop_kind,
        "language": language,
        "verified": True,
    }
    if error:
        rec["error"] = True
    return rec


def _legit(n, language="python", fp=0):
    """n verified legit records in one language, `fp` of them false-positive."""
    flagged = [_rec(False, True, language=language) for _ in range(fp)]
    clean = [_rec(False, False, language=language) for _ in range(n - fp)]
    return flagged + clean


def _slop(n, kind, tp):
    """n verified Slop records of one stratum, `tp` of them caught."""
    caught = [_rec(True, True, kind) for _ in range(tp)]
    missed = [_rec(True, False, kind) for _ in range(n - tp)]
    return caught + missed


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
    assert strata["obvious"] == {"tp": 1, "condemned": 1, "n": 2, "tpr": 0.5}
    assert strata["subtle"] == {"tp": 2, "condemned": 2, "n": 2, "tpr": 1.0}


def test_needs_info_counts_as_a_protective_catch():
    # A slop case the bot flags (needs-info) but does not condemn still counts as
    # caught: protective TPR credits any action, while `condemned` stays separate.
    records = [
        _rec(True, True, "subtle"),  # condemned
        _rec(True, False, "subtle", predicted_flagged=True),  # needs-info flag
        _rec(True, False, "subtle"),  # missed entirely (passed as legit)
    ]
    strata = tpr_strata(records)
    assert strata["subtle"]["tp"] == 2  # condemn + flag both count as caught
    assert strata["subtle"]["condemned"] == 1
    assert strata["subtle"]["tpr"] == pytest.approx(2 / 3)


def test_soft_fpr_counts_needs_info_on_legit_separately_from_hard():
    from eval_replay import soft_fpr

    records = [
        _rec(False, True),  # legit condemned -> HARD false positive
        _rec(False, False, predicted_flagged=True),  # legit flagged -> SOFT false positive
        _rec(False, False),  # legit passed clean
    ]
    assert pooled_fpr(records) == {"fp": 1, "n": 3, "fpr": pytest.approx(1 / 3)}
    assert soft_fpr(records) == {"soft_fp": 1, "n": 3, "fpr": pytest.approx(1 / 3)}


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


def test_fpr_gate_passes_at_or_below_ceiling():
    assert fpr_gate({"fp": 0, "n": 50, "fpr": 0.0})["passed"] is True
    assert fpr_gate({"fp": 1, "n": 50, "fpr": 0.02})["passed"] is True  # exactly the ceiling
    assert fpr_gate({"fp": 3, "n": 50, "fpr": 0.06})["passed"] is False


def test_fpr_gate_fails_on_unmeasured_fpr():
    # no verified legit cases leaves FPR None; absence of evidence is not a pass
    assert fpr_gate({"fp": 0, "n": 0, "fpr": None})["passed"] is False


def test_fpr_gate_ceiling_is_a_parameter():
    assert fpr_gate({"fp": 1, "n": 50, "fpr": 0.02}, ceiling=0.01)["passed"] is False


def test_coverage_gate_passes_with_no_gaps():
    assert coverage_gate({"by_language": {"python": 3}, "gaps": []})["passed"] is True


def test_coverage_gate_fails_with_gap():
    cg = coverage_gate({"by_language": {"python": 3}, "gaps": ["go"]})
    assert cg["passed"] is False
    assert cg["gaps"] == ["go"]


def _passing_slice():
    # clean legit pile (no FP), both Slop strata above floor, python exercised
    return _legit(50, "python") + _slop(10, "obvious", 10) + _slop(10, "subtle", 6)


def test_gate_decision_passes_when_all_dimensions_satisfied():
    gate = gate_decision(_passing_slice(), frozenset({"python"}))
    assert gate["passed"] is True
    assert gate["fpr_gate"]["passed"] is True
    assert gate["tpr_gate"]["passed"] is True
    assert gate["coverage_gate"]["passed"] is True


def test_gate_decision_fails_when_pooled_fpr_over_ceiling():
    # 2 false positives in 50 legit = 4%, over the 2% ceiling — the hard gate fails
    sl = _legit(50, "python", fp=2) + _slop(10, "obvious", 10) + _slop(10, "subtle", 6)
    gate = gate_decision(sl, frozenset({"python"}))
    assert gate["fpr_gate"]["passed"] is False
    assert gate["passed"] is False


def test_gate_decision_fails_when_subtle_below_floor():
    # a strong blended TPR cannot carry the gate when the subtle stratum is thin
    sl = _legit(50, "python") + _slop(20, "obvious", 20) + _slop(10, "subtle", 1)
    gate = gate_decision(sl, frozenset({"python"}))
    assert gate["tpr_gate"]["blended_ok"] is True
    assert gate["tpr_gate"]["subtle_ok"] is False
    assert gate["passed"] is False


def test_gate_decision_fails_on_acted_on_language_with_zero_legit():
    # go is acted-on but no verified legit case exercises it, so it is a coverage
    # gap that blocks the pass even though every rate clears its bound
    sl = _passing_slice()
    gate = gate_decision(sl, frozenset({"python", "go"}))
    assert gate["coverage_gate"]["passed"] is False
    assert gate["coverage_gate"]["gaps"] == ["go"]
    assert gate["passed"] is False


def test_gate_decision_thresholds_are_parameters():
    # the same slice flips pass -> fail when the ceiling is tightened below its
    # measured FPR, proving thresholds are parameters not literals buried in scoring
    sl = _legit(50, "python", fp=1) + _slop(10, "obvious", 10) + _slop(10, "subtle", 6)
    assert gate_decision(sl, frozenset({"python"}))["passed"] is True
    tightened = gate_decision(sl, frozenset({"python"}), fpr_ceiling=0.01)
    assert tightened["fpr_gate"]["passed"] is False
    assert tightened["passed"] is False


def test_power_warnings_flags_under_n_pooled_legit():
    pooled = {"fp": 0, "n": 10, "fpr": 0.0}
    coverage = {"by_language": {"python": 10}, "gaps": []}
    strata = tpr_strata(_slop(30, "obvious", 30) + _slop(30, "subtle", 20))
    warns = power_warnings(pooled, coverage, strata, frozenset({"python"}))
    assert [w["tier"] for w in warns] == ["pooled legit"]
    assert warns[0]["n"] == 10
    assert warns[0]["min"] == 150


def test_power_warnings_flags_thin_per_language_but_not_zero_gap():
    # a language with a few legit cases is under-powered (a warning); a language
    # with zero is a coverage gap (a gate failure), so it does not warn here
    pooled = {"fp": 0, "n": 200, "fpr": 0.0}
    coverage = {"by_language": {"python": 195, "go": 5}, "gaps": ["rust"]}
    strata = tpr_strata(_slop(30, "obvious", 30) + _slop(30, "subtle", 20))
    warns = power_warnings(pooled, coverage, strata, frozenset({"python", "go", "rust"}))
    tiers = [w["tier"] for w in warns]
    assert "legit:go" in tiers  # 5 below the per-language minimum
    assert "legit:rust" not in tiers  # zero is a gap, not a warning
    assert "legit:python" not in tiers  # well above the minimum


def test_power_warnings_flags_thin_slop_strata():
    pooled = {"fp": 0, "n": 200, "fpr": 0.0}
    coverage = {"by_language": {"python": 200}, "gaps": []}
    strata = tpr_strata(_slop(10, "obvious", 10) + _slop(5, "subtle", 3))
    warns = power_warnings(pooled, coverage, strata, frozenset({"python"}))
    tiers = [w["tier"] for w in warns]
    assert "slop:obvious" in tiers
    assert "slop:subtle" in tiers


def test_power_warnings_empty_when_all_tiers_meet_min():
    pooled = {"fp": 0, "n": 200, "fpr": 0.0}
    coverage = {"by_language": {"python": 200}, "gaps": []}
    strata = tpr_strata(_slop(30, "obvious", 30) + _slop(30, "subtle", 20))
    warns = power_warnings(pooled, coverage, strata, frozenset({"python"}))
    assert warns == []


def test_under_n_slice_warns_without_failing_gate():
    # a tiny but clean Slice clears every gate dimension yet warns on power: a
    # warning is distinct from a gate failure
    sl = _legit(5, "python") + _slop(2, "obvious", 2) + _slop(2, "subtle", 1)
    gate = gate_decision(sl, frozenset({"python"}))
    warns = power_warnings(
        gate["pooled_fpr"], gate["legit_coverage"], gate["tpr_strata"], frozenset({"python"})
    )
    assert gate["passed"] is True
    assert warns  # under-powered on pooled legit and both Slop strata


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
