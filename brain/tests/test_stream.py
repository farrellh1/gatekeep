import os
from pathlib import Path

import pytest

from core.models_config import load_models_config
from eval_corpus import CorpusError, parse_case
from eval_scoring import run_once
from eval_stream import (
    funnel,
    load_stream_case,
    report_header,
    stream_record,
    worksheet_rows,
)

STREAM_FIXTURE = Path(__file__).parent / "stream" / "acme_widgets_7.json"
CLONE = str(Path(__file__).parent / "fixtures" / "clone")
_DEFAULT_PROVIDER, _ = load_models_config().resolve("default")


def _stream_case():
    """A Stream case: only what offline replay needs plus provenance. It omits
    label/verified/slop_kind on purpose, so the Corpus loader must reject it."""
    return {
        "event": {
            "delivery_id": "stream-1",
            "kind": "pull_request",
            "action": "opened",
            "repo": {"full_name": "acme/widgets", "default_branch": "main"},
            "number": 7,
            "title": "Add retry helper",
            "body": "small helper",
            "author": {"login": "alice", "account_age_days": 5, "is_first_time_contributor": True},
            "diff": "diff --git a/x b/x",
            "changed_files": ["x"],
            "ci_status": "pending",
            "existing_issues": [],
            "linked_issues": [],
        },
        "config_yaml": None,
        "snapshot": "snapshots/abc.json",
        "provenance": {
            "repo": "acme/widgets",
            "number": 7,
            "url": "https://github.com/acme/widgets/pull/7",
            "author": "alice",
            "ci_forced_pending": True,
        },
    }


def test_corpus_loader_rejects_a_stream_case():
    """The Stream contract is independent of the Corpus contract: a Stream case
    carries no label/verified/source, so parse_case must refuse it rather than
    coerce an unlabeled real event into the gate."""
    with pytest.raises(CorpusError):
        parse_case(_stream_case())


def test_stream_case_presents_pending_ci():
    """Production fires the webhook the instant a PR opens, before CI resolves --
    the only state in which the first-contribution prior can fire. A Stream case
    is stamped ci_status="pending" so scoring is not optimistically suppressed by
    hindsight CI."""
    case = load_stream_case(STREAM_FIXTURE)
    assert case["event"]["ci_status"] == "pending"


def _rec(outcome, **kw):
    base = {"outcome": outcome, "reasons": [], "trace_id": "t", "provenance": {}}
    base.update(kw)
    return base


def test_funnel_counts_every_outcome():
    """The readout reports the whole funnel, not only the fires, so the maintainer
    sees the full skip/legit/needs-info/slop spread of the run."""
    records = [
        _rec("skip"),
        _rec("legit"),
        _rec("legit"),
        _rec("needs-info"),
        _rec("slop"),
        _rec("error"),
    ]
    assert funnel(records) == {
        "skip": 1,
        "legit": 2,
        "needs-info": 1,
        "slop": 1,
        "error": 1,
    }


def _prov(login):
    return {
        "repo": f"acme/{login}",
        "number": 7,
        "url": f"https://github.com/acme/{login}/pull/7",
        "author": login,
        "ci_forced_pending": True,
    }


def test_worksheet_lists_exactly_the_fires_with_provenance():
    """The worksheet is the hand-adjudication surface: every fire (needs-info or
    slop) with the link and trace a human needs to judge it, and nothing else. A
    pass on a good PR is already correct, so passes are excluded."""
    records = [
        _rec("skip", provenance=_prov("skipped")),
        _rec("legit", provenance=_prov("good")),
        _rec(
            "needs-info",
            reasons=["thin description"],
            trace_id="stream-acme-q-7",
            provenance=_prov("questioned"),
        ),
        _rec(
            "slop",
            reasons=["no real change", "ai-pattern"],
            trace_id="stream-acme-bad-7",
            provenance=_prov("bad"),
        ),
    ]
    rows = worksheet_rows(records, trace_dir="/traces")

    assert [r["outcome"] for r in rows] == ["needs-info", "slop"]
    info, slop = rows
    assert info["url"] == "https://github.com/acme/questioned/pull/7"
    assert info["author"] == "questioned"
    assert info["reasons"] == ["thin description"]
    assert info["trace_path"] == "/traces/stream-acme-q-7.json"
    # empty columns the maintainer fills in by hand while reading each PR
    assert info["human_verdict"] == ""
    assert info["mistake"] == ""
    assert slop["reasons"] == ["no real change", "ai-pattern"]
    assert slop["trace_path"] == "/traces/stream-acme-bad-7.json"


def test_report_header_states_scope_and_captions_corpus_catch_rate():
    """The pilot can be misread as a full accuracy claim, so its header must state
    plainly that it measures false positives only and no wild catch-rate. The one
    number the Stream cannot give -- catch-rate -- is sourced honestly from the
    Corpus and captioned as label-limited in the wild rather than faked."""
    header = report_header(corpus_tpr="72.7%")
    assert "false positives only" in header
    assert "no wild catch-rate" in header
    assert "72.7%" in header
    assert "corpus-measured, label-limited in the wild" in header


def test_stream_record_carries_provenance_into_the_outcome():
    """A Stream record joins the Brain's run outcome to the case's provenance, so
    a fire can be traced back to the PR a human must open and judge. The Stream
    has no label, so unlike a Corpus record it carries no expected/positive."""
    case = load_stream_case(STREAM_FIXTURE)
    run = {"outcome": "needs-info", "reasons": ["thin"], "trace_id": "stream-acme-widgets-7"}
    record = stream_record(case, run)
    assert record["outcome"] == "needs-info"
    assert record["reasons"] == ["thin"]
    assert record["trace_id"] == "stream-acme-widgets-7"
    assert record["provenance"]["url"] == "https://github.com/acme/widget/pull/7"
    assert "expected_positive" not in record


@pytest.mark.golden
@pytest.mark.skipif(
    not os.environ.get(_DEFAULT_PROVIDER.api_key_env),
    reason=f"stream smoke run hits the real model; set ${_DEFAULT_PROVIDER.api_key_env} to run",
)
def test_stream_case_drives_the_brain_end_to_end():
    """The pilot's verdicts mean what production verdicts would only if the Stream
    rides the same run_once -> graph.process seam. This smoke run proves a Stream
    case (carrying no Corpus contract fields) drives the Brain to a real verdict."""
    rec = run_once(str(STREAM_FIXTURE), CLONE)
    assert rec["outcome"] in ("skip", "legit", "needs-info", "slop")
