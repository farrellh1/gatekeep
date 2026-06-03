from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass


class CorpusError(ValueError):
    """A Corpus case that does not satisfy the contract. Raised at load time so a
    malformed harvest fails loudly instead of silently skewing a rate."""


# A slop label rests on one of these closed-unmerged quality-rejection signals.
# Raw `closed` (mostly legit PRs that did not land) and `locked_as_spam` (a
# population the product does not target) are deliberately absent, so neither can
# stand in for slop.
QUALITY_REJECTION_SIGNALS = frozenset(
    {"rejection_label", "dismissive_close", "fast_close_no_engagement"}
)
ORIGINS = frozenset({"harvested", "synthetic"})
LABELS = frozenset({"legit", "slop"})


@dataclass(frozen=True)
class Source:
    repo: str
    number: int
    origin: str  # "harvested" | "synthetic"
    signal: str


@dataclass(frozen=True)
class CorpusCase:
    label: str  # "legit" | "slop"
    verified: bool
    slop_kind: str | None  # "obvious" | "subtle" | None
    language: str
    source: Source
    snapshot: str
    event: dict
    config_yaml: str | None


def _require(data: dict, key: str):
    if key not in data:
        raise CorpusError(f"missing required field {key!r}")
    return data[key]


def parse_case(data: dict) -> CorpusCase:
    label = _require(data, "label")
    verified = _require(data, "verified")
    language = _require(data, "language")
    src = _require(data, "source")
    snapshot = _require(data, "snapshot")
    event = _require(data, "event")

    if label not in LABELS:
        raise CorpusError(f"label must be 'legit' or 'slop', got {label!r}")

    origin = _require(src, "origin")
    if origin not in ORIGINS:
        raise CorpusError(f"source.origin must be 'harvested' or 'synthetic', got {origin!r}")

    slop_kind = data.get("slop_kind")
    if slop_kind is not None and slop_kind not in ("obvious", "subtle"):
        raise CorpusError(f"slop_kind must be 'obvious', 'subtle', or null, got {slop_kind!r}")
    if label == "legit" and slop_kind is not None:
        raise CorpusError("slop_kind must be null on a legit case")

    signal = _require(src, "signal")
    if label == "slop" and origin == "harvested" and signal not in QUALITY_REJECTION_SIGNALS:
        raise CorpusError(
            f"slop case needs a quality-rejection signal or synthetic origin; "
            f"signal {signal!r} cannot produce a slop label"
        )
    return CorpusCase(
        label=label,
        verified=verified,
        slop_kind=data.get("slop_kind"),
        language=language,
        source=Source(
            repo=_require(src, "repo"),
            number=_require(src, "number"),
            origin=origin,
            signal=signal,
        ),
        snapshot=snapshot,
        event=event,
        config_yaml=data.get("config_yaml"),
    )


def load_case(path: str | os.PathLike) -> CorpusCase:
    with open(path) as f:
        data = json.load(f)
    try:
        return parse_case(data)
    except CorpusError as exc:
        raise CorpusError(f"{os.fspath(path)}: {exc}") from exc


def load_corpus(corpus_dir: str | os.PathLike) -> list[CorpusCase]:
    paths = sorted(glob.glob(os.path.join(os.fspath(corpus_dir), "*.json")))
    return [load_case(p) for p in paths]
