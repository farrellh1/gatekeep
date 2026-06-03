import os
from pathlib import Path

import pytest

from core.models_config import load_models_config
from eval_corpus import load_corpus, parse_case
from eval_replay import case_record, slice_verified, tpr_gate, tpr_strata
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


def _rec(expected_positive, predicted_positive, slop_kind=None):
    return {
        "name": "x",
        "expected_positive": expected_positive,
        "predicted_positive": predicted_positive,
        "slop_kind": slop_kind,
        "verified": True,
    }


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
