"""Score the Brain over a Stream of unlabeled real PRs to observe false positives.

A Stream is a deliberately lightweight population, the sibling of the Corpus: real
events sampled for false-positive observation, with no outcome label and no human
pre-verification. A Stream case carries only what offline replay needs (`event`,
optional `config_yaml`, `snapshot`) plus a `provenance` block for hand-adjudication.
It never satisfies the Corpus case contract and never feeds the go-public gate; it
rides the same `run_once` -> `graph.process` seam production uses, then this thin
reporter counts verdicts and lists the fires for a human to read.

This measures false positives only -- how often the firewall bothers a good
contribution on traffic it did not hand-pick. It measures no wild catch-rate: the
hand-check reads only the fires, because a pass on a good PR is already correct and
a missed slop is a catch-rate question this pilot deliberately does not ask.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys

from dotenv import load_dotenv

from core.models_config import load_models_config
from core.trace import configure_logging
from eval_scoring import run_once

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
# Real harvested Stream cases and the readout live here, gitignored: they
# reference third-party PR content that must not be committed.
STREAM_DIR = str(ROOT / "stream")
TRACE_DIR = str(ROOT / "stream" / "traces")
os.environ.setdefault("GATEKEEP_TRACE_DIR", TRACE_DIR)
# One fixture clone backs replay; a case's `snapshot` reference, when present, is
# hydrated by run_once instead, so the Brain reads the repo as it stood at open.
CLONE = str(ROOT / "tests" / "fixtures" / "clone")
# The corpus catch-rate the header cites. The Stream cannot measure catch-rate in
# the wild, so this number is sourced from the verified Corpus gate and captioned
# as label-limited. A parameter, not a literal, so a re-measured Corpus updates it.
CORPUS_TPR = "72.7%"

SLOP = "slop"
NEEDS_INFO = "needs-info"
# A "fire" is any protective action the Brain takes on a PR: a harsh condemn (slop)
# or a mild question (needs-info). Only fires land on the hand-check worksheet.
FIRE_OUTCOMES = (NEEDS_INFO, SLOP)


# Every outcome the Brain can reach over a Stream case, in funnel order. `error`
# is a run that threw rather than a verdict, kept visible so a broken run is never
# silently dropped from the count.
FUNNEL_OUTCOMES = ("skip", "legit", NEEDS_INFO, SLOP, "error")


def funnel(records: list[dict]) -> dict:
    """Count every outcome across the run, reporting the whole funnel rather than
    only the fires. Outcomes are seeded at zero so the four verdicts (and error)
    always appear, even when a run produced none of a given kind."""
    counts = {outcome: 0 for outcome in FUNNEL_OUTCOMES}
    for record in records:
        outcome = record["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def worksheet_rows(records: list[dict], trace_dir: str) -> list[dict]:
    """One row per fire (needs-info or slop), in record order, for hand-checking.
    Each row carries the PR link and author from provenance, the Brain's outcome
    and stated reasons, and the trace path so the maintainer can read why it fired.
    `human_verdict` (good/junk) and `mistake` (y/n) start empty -- the maintainer
    fills them while reading each PR. Passes are excluded: a pass on a good PR is
    already correct and needs no review."""
    rows = []
    for record in records:
        if record["outcome"] not in FIRE_OUTCOMES:
            continue
        provenance = record.get("provenance", {})
        rows.append(
            {
                "url": provenance.get("url"),
                "author": provenance.get("author"),
                "outcome": record["outcome"],
                "reasons": record.get("reasons", []),
                "trace_path": os.path.join(trace_dir, f"{record['trace_id']}.json"),
                "human_verdict": "",
                "mistake": "",
            }
        )
    return rows


def stream_record(case: dict, run: dict) -> dict:
    """Join one run's outcome to the case's provenance. Unlike the Corpus record
    there is no label, so no expected/positive and no voting: the Stream is a
    single run per case, scored only for whether it fired and -- if it did -- where
    to read the PR. Provenance rides along so the worksheet can cite the PR link,
    author, and trace without re-opening the case file."""
    return {
        "outcome": run["outcome"],
        "reasons": run.get("reasons", []),
        "trace_id": run.get("trace_id"),
        "provenance": case.get("provenance", {}),
    }


def report_header(corpus_tpr: str) -> str:
    """The readout's framing. It states the pilot's scope -- false positives only,
    no wild catch-rate -- so the report cannot be read as a full accuracy claim,
    and it cites the Corpus catch-rate with a caption that admits the Stream cannot
    measure catch-rate in the wild, rather than leaving the number unsourced."""
    return (
        "GATEKEEP STREAM PILOT\n" + "=" * 72 + "\n"
        "Measures false positives only: how often the firewall bothers a good\n"
        "contribution on real PRs it did not hand-pick.\n"
        "It measures no wild catch-rate -- the hand-check reads only the fires.\n"
        f"Corpus catch-rate: {corpus_tpr} (corpus-measured, label-limited in the wild).\n"
    )


def load_stream_case(path: str | os.PathLike) -> dict:
    """Read a Stream case file. Unlike the Corpus loader there is no contract to
    enforce: a Stream case is unlabeled by design, so its fields are passed through
    as-is for `run_once` to drive and for the worksheet to cite."""
    with open(path) as f:
        return json.load(f)


def report(funnel_counts: dict, rows: list[dict], n: int, model: str) -> None:
    """Print the funnel and the hand-check worksheet. The two false-positive rates
    are computed by hand off this worksheet, not here: only the maintainer, reading
    each fired-on PR, can say whether a fire was a mistake."""
    print(report_header(CORPUS_TPR))
    print(f"Stream: {n} cases | 1 run/case | model: {model}\n")

    print("Funnel (every outcome):")
    for outcome in FUNNEL_OUTCOMES:
        print(f"  {outcome:<11} {funnel_counts.get(outcome, 0)}")

    fires = funnel_counts.get(NEEDS_INFO, 0) + funnel_counts.get(SLOP, 0)
    print(f"\nFires to hand-check ({fires}): each is a PR to open and judge good/junk.")
    print(f"  {'outcome':<11} {'author':<20} url")
    for row in rows:
        print(f"  {row['outcome']:<11} {row['author'] or '?':<20} {row['url']}")
        print(f"    reasons: {'; '.join(row['reasons']) or '(none)'}")
        print(f"    trace:   {row['trace_path']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Drive the Brain over a Stream of unlabeled real PRs (one run "
        "per case) and emit a funnel plus a fire-only hand-check worksheet."
    )
    ap.add_argument("--stream", default=STREAM_DIR, help="directory of Stream case JSON files")
    ap.add_argument(
        "--out",
        default=str(ROOT / "stream" / "readout.json"),
        help="path to write the machine-readable readout",
    )
    args = ap.parse_args()
    configure_logging()

    provider, model_name = load_models_config().resolve("default")
    if not os.environ.get(provider.api_key_env):
        print(f"${provider.api_key_env} not set; the pilot hits the real model.", file=sys.stderr)
        return 2

    paths = sorted(glob.glob(os.path.join(args.stream, "*.json")))
    if not paths:
        print(f"no Stream cases found under {args.stream}/*.json", file=sys.stderr)
        return 2

    model = f"{provider.name}:{model_name}"
    records = []
    for path in paths:
        case = load_stream_case(path)
        run = run_once(path, CLONE)
        records.append(stream_record(case, run))

    funnel_counts = funnel(records)
    rows = worksheet_rows(records, os.environ["GATEKEEP_TRACE_DIR"])
    report(funnel_counts, rows, len(records), model)

    artifact = {
        "model": model,
        "stream_n": len(records),
        "corpus_tpr": CORPUS_TPR,
        "funnel": funnel_counts,
        "worksheet": rows,
        "records": records,
    }
    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(json.dumps(artifact, indent=2))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
