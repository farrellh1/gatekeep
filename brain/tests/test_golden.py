import glob
import json
import os
import pathlib

import pytest

from brain import graph

CLONE = str(pathlib.Path(__file__).parent / "fixtures" / "clone")
CASES = sorted(glob.glob(str(pathlib.Path(__file__).parent / "golden" / "*" / "*.json")))

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="golden set hits the real model; set OPENROUTER_API_KEY to run",
    ),
]


@pytest.mark.parametrize("path", CASES, ids=[pathlib.Path(p).stem for p in CASES])
def test_golden_case(path):
    case = json.load(open(path))
    bucket = pathlib.Path(path).parent.name
    event = case["event"]
    event["repo"]["clone_path"] = CLONE

    result = graph.process(event, case.get("config_yaml"))
    assert result["intake"]["route"] != "skip", f"{path}: unexpectedly skipped"

    label = result["verdict"]["label"]
    if bucket == "slop":
        assert label == "slop", f"{path}: expected slop, got {label} ({result['verdict']['reasons']})"
    else:
        assert label != "slop", f"{path}: legit control flagged as slop ({result['verdict']['reasons']})"
