from eval_scoring import aggregate, confusion, rate


def _runs(*outcomes, confidence=None):
    return [{"outcome": o, "confidence": confidence, "reasons": []} for o in outcomes]


def test_aggregate_takes_majority_vote():
    rec = aggregate("slop/x", expected_positive=True, runs=_runs("slop", "slop", "legit"))
    assert rec["vote"] == "slop"
    assert rec["predicted_positive"] is True
    assert rec["expected_positive"] is True
    assert rec["flaky"] is True


def test_aggregate_unanimous_is_not_flaky():
    rec = aggregate("legit/y", expected_positive=False, runs=_runs("legit", "legit"))
    assert rec["vote"] == "legit"
    assert rec["predicted_positive"] is False
    assert rec["flaky"] is False
    assert rec["agreement"] == 1.0


def test_aggregate_all_errored_marks_error():
    rec = aggregate("slop/z", expected_positive=True, runs=_runs("error", "error"))
    assert rec["error"]
    assert rec["predicted_positive"] is None


def test_confusion_counts_quadrants():
    records = [
        {"expected_positive": True, "predicted_positive": True},  # tp
        {"expected_positive": True, "predicted_positive": False},  # fn
        {"expected_positive": False, "predicted_positive": True},  # fp
        {"expected_positive": False, "predicted_positive": False},  # tn
        {"error": "all runs errored"},  # excluded
    ]
    assert confusion(records) == {"tp": 1, "fp": 1, "fn": 1, "tn": 1, "errored": 1}


def test_rate_is_none_on_empty_denominator():
    assert rate(0, 0) is None
    assert rate(1, 4) == 0.25
