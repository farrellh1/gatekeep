import glob
import json
import os
import pathlib

import pytest

from core import graph
from core.models_config import load_models_config

CLONE = str(pathlib.Path(__file__).parent / "fixtures" / "clone")
_DEFAULT_PROVIDER, _ = load_models_config().resolve("default")
CASES = sorted(
    p
    for p in glob.glob(str(pathlib.Path(__file__).parent / "golden" / "*" / "*.json"))
    if pathlib.Path(p).parent.name in ("legit", "slop")
)

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not os.environ.get(_DEFAULT_PROVIDER.api_key_env),
        reason=f"golden set hits the real model; set ${_DEFAULT_PROVIDER.api_key_env} to run",
    ),
]


@pytest.mark.parametrize("path", CASES, ids=[pathlib.Path(p).stem for p in CASES])
def test_golden_case(path):
    with open(path) as f:
        case = json.load(f)
    bucket = pathlib.Path(path).parent.name
    event = case["event"]
    event["repo"]["clone_path"] = CLONE

    result = graph.process(event, case.get("config_yaml"))
    assert result["intake"]["route"] != "skip", f"{path}: unexpectedly skipped"

    label = result["verdict"]["label"]
    if bucket == "slop":
        assert label == "slop", (
            f"{path}: expected slop, got {label} ({result['verdict']['reasons']})"
        )
    else:
        assert label != "slop", (
            f"{path}: legit control flagged as slop ({result['verdict']['reasons']})"
        )
