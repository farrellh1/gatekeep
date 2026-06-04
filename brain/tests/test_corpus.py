import json
from pathlib import Path

import pytest

from eval_corpus import CorpusError, load_corpus, parse_case

CORPUS = Path(__file__).parent / "corpus"


def _well_formed(**overrides):
    """A minimal well-formed legit case dict; override one field per test."""
    data = {
        "label": "legit",
        "verified": True,
        "slop_kind": None,
        "language": "python",
        "source": {
            "repo": "acme/widget",
            "number": 201,
            "origin": "harvested",
            "signal": "merged",
        },
        "snapshot": "snapshots/acme-widget-201.json",
        "config_yaml": None,
        "event": {"delivery_id": "c-legit-01", "kind": "pull_request"},
    }
    data.update(overrides)
    return data


def _slop(**overrides):
    """A minimal well-formed slop case: harvested with a quality-rejection signal."""
    data = _well_formed(
        label="slop",
        slop_kind="obvious",
        source={
            "repo": "acme/widget",
            "number": 101,
            "origin": "harvested",
            "signal": "rejection_label",
        },
    )
    src_override = overrides.pop("source", None)
    data.update(overrides)
    if src_override is not None:
        data["source"] = src_override
    return data


def test_well_formed_case_loads_with_metadata_intact():
    case = parse_case(_well_formed())
    assert case.label == "legit"
    assert case.verified is True
    assert case.slop_kind is None
    assert case.language == "python"
    assert case.source.repo == "acme/widget"
    assert case.source.number == 201
    assert case.source.origin == "harvested"
    assert case.snapshot == "snapshots/acme-widget-201.json"
    assert case.event["delivery_id"] == "c-legit-01"


@pytest.mark.parametrize("missing", ["label", "verified", "language"])
def test_missing_required_field_fails_loudly(missing):
    data = _well_formed()
    del data[missing]
    with pytest.raises(CorpusError, match=missing):
        parse_case(data)


def test_slop_kind_on_legit_case_fails_loudly():
    # the stratum is meaningful only for slop; a legit case carrying one is a
    # harvest that mislabelled its own population
    with pytest.raises(CorpusError, match="slop_kind"):
        parse_case(_well_formed(slop_kind="obvious"))


def test_unknown_slop_kind_value_fails_loudly():
    data = _slop(slop_kind="kinda")
    with pytest.raises(CorpusError, match="slop_kind"):
        parse_case(data)


def test_harvested_slop_loads_with_quality_rejection_signal():
    case = parse_case(_slop())
    assert case.label == "slop"
    assert case.slop_kind == "obvious"
    assert case.source.signal == "rejection_label"


def test_synthetic_slop_loads_regardless_of_signal():
    case = parse_case(
        _slop(
            source={
                "repo": "acme/widget",
                "number": 9,
                "origin": "synthetic",
                "signal": "manufactured-plausible-pr",
            }
        )
    )
    assert case.source.origin == "synthetic"


@pytest.mark.parametrize("signal", ["closed", "locked_as_spam"])
def test_harvested_slop_without_quality_rejection_signal_fails_loudly(signal):
    # raw `closed` is mostly legit PRs that did not land; `locked_as_spam` names a
    # population the product does not target. Neither can stand in for slop.
    data = _slop(
        source={"repo": "acme/widget", "number": 101, "origin": "harvested", "signal": signal}
    )
    with pytest.raises(CorpusError, match="signal"):
        parse_case(data)


def test_unknown_label_fails_loudly():
    with pytest.raises(CorpusError, match="label"):
        parse_case(_well_formed(label="maybe"))


def test_unknown_origin_fails_loudly():
    data = _well_formed(
        source={"repo": "acme/widget", "number": 1, "origin": "scraped", "signal": "merged"}
    )
    with pytest.raises(CorpusError, match="origin"):
        parse_case(data)


def test_load_corpus_reads_committed_fixture_cases():
    cases = load_corpus(CORPUS)
    by_id = {c.event["delivery_id"]: c for c in cases}
    # Spot-check a representative legit case (psf/requests) and a slop case
    # (openclaw/openclaw). All 16 committed cases are verified=True.
    assert by_id["harvest-psf-requests-7433"].label == "legit"
    assert by_id["harvest-psf-requests-7433"].verified is True
    assert by_id["harvest-openclaw-openclaw-90251"].label == "slop"


def test_load_corpus_names_the_offending_file(tmp_path):
    (tmp_path / "broken.json").write_text(json.dumps({"label": "legit"}))
    with pytest.raises(CorpusError, match="broken.json"):
        load_corpus(tmp_path)
