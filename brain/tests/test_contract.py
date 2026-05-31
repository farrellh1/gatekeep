import json
import pathlib

from core.schemas import NormalizedEvent

SAMPLE = pathlib.Path(__file__).parent / "contract" / "normalized_event.sample.json"


def test_sample_validates_as_normalized_event():
    data = json.loads(SAMPLE.read_text())
    ev = NormalizedEvent(**data)
    assert ev.kind == "pull_request"
    assert ev.repo.clone_path.endswith("gatekeep-dogfood-sample")


def test_sample_roundtrips():
    data = json.loads(SAMPLE.read_text())
    ev = NormalizedEvent(**data)
    assert ev.model_dump()["changed_files"] == ["src/auth.py"]
