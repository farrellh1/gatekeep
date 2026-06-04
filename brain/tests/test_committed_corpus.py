"""Tests that the verified Corpus lives in the committed corpus directory the eval reads.

These tests are the red phase: they fail until the 16 verified cases, their
snapshots, and the dropped directory are all committed into the right paths.

Every test states the premise it relies on at the top of the function body.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval_corpus import load_corpus

# The corpus directory eval_replay and eval_corpus both read, derived from the
# eval_corpus loader's own path constant (CORPUS in test_replay.py and test_corpus.py).
CORPUS = Path(__file__).parent / "corpus"
SNAPSHOTS = CORPUS / "snapshots"
DROPPED = CORPUS / "dropped"

# The 16 verified delivery IDs that must live in CORPUS after migration.
# Derived from the backup case JSONs; each ID follows the pattern
# "harvest-<owner>-<repo>-<number>".
EXPECTED_DELIVERY_IDS = frozenset(
    {
        "harvest-openclaw-openclaw-90081",
        "harvest-openclaw-openclaw-90183",
        "harvest-openclaw-openclaw-90236",
        "harvest-openclaw-openclaw-90251",
        "harvest-pallets-flask-5903",
        "harvest-pallets-flask-5917",
        "harvest-pallets-flask-5928",
        "harvest-pallets-flask-5945",
        "harvest-pallets-flask-5962",
        "harvest-pallets-flask-6013",
        "harvest-psf-requests-7433",
        "harvest-psf-requests-7436",
        "harvest-psf-requests-7437",
        "harvest-psf-requests-7441",
        "harvest-psf-requests-7460",
        "harvest-psf-requests-7490",
    }
)

# The 6 dropped case filenames that must sit under CORPUS/dropped/ but NOT be
# globbed by load_corpus (which reads only CORPUS/*.json).
DROPPED_FILENAMES = frozenset(
    {
        "openclaw_openclaw_90087.json",
        "openclaw_openclaw_90127.json",
        "openclaw_openclaw_90137.json",
        "openclaw_openclaw_90189.json",
        "openclaw_openclaw_90225.json",
        "openclaw_openclaw_90253.json",
    }
)

# Content-hash filenames for the snapshots the 16 cases reference. Derived
# from the backup case JSONs; each is the bare filename (no directory prefix).
EXPECTED_SNAPSHOT_FILENAMES = frozenset(
    {
        "a2c06dc58a4fb4ef696fd36ec0cd04a658b9aa474f05aa8442b63e33028020a5.json",
        "2d4473f3f3cffdedd1ac6b738fad3ec50d63fd2035c09f0fee9523c7f67dcdd3.json",
        "c1d4892570d4555a15c568cb623977500f067c3cb441c81372b7f781d53ee13e.json",
        "e066689b5f7f87ed4dd304d5d65422ebc763d15c4e4024082d24c88ee03441ff.json",
    }
)


# ---------------------------------------------------------------------------
# Corpus directory structure
# ---------------------------------------------------------------------------


def test_corpus_directory_contains_exactly_the_expected_number_of_cases():
    """Premise: CORPUS/*.json globs exactly the 16 verified cases; the
    existing 3 toy cases are replaced or the 16 are appended alongside them.
    The spec says 16 verified cases live in the committed corpus directory.
    References: brain/tests/corpus/ (glob target), eval_corpus.load_corpus.
    """
    case_files = sorted(CORPUS.glob("*.json"))
    # The toy fixtures (legit_01_bugfix.json, legit_02_unverified.json,
    # slop_01_hallucinated_symbol.json) may stay or go — the spec replaces
    # the ephemeral set with the committed verified set.  The test enforces
    # the post-migration count.
    assert len(case_files) == 16, (
        f"Expected 16 case JSON files in {CORPUS}, found {len(case_files)}: "
        + ", ".join(f.name for f in case_files)
    )


def test_all_16_verified_cases_are_present_by_delivery_id():
    """Premise: every committed case JSON carries the delivery_id from which
    its identity is derived, following the 'harvest-<owner>-<repo>-<number>'
    pattern.
    References: eval_corpus.load_corpus, CorpusCase.event['delivery_id'].
    """
    cases = load_corpus(CORPUS)
    found_ids = {c.event["delivery_id"] for c in cases}
    missing = EXPECTED_DELIVERY_IDS - found_ids
    assert not missing, f"Missing delivery IDs in committed corpus: {missing}"


def test_every_case_passes_the_eval_corpus_loader_contract():
    """Premise: parse_case raises CorpusError on any schema violation, so
    loading all cases through load_corpus is a full contract check.
    References: eval_corpus.load_corpus, eval_corpus.parse_case.
    """
    # load_corpus raises on the first bad case with the filename embedded in
    # the error; this test surfaces any malformed committed case immediately.
    cases = load_corpus(CORPUS)
    assert len(cases) == 16


# ---------------------------------------------------------------------------
# Labeling counts
# ---------------------------------------------------------------------------


def test_corpus_contains_12_legit_and_4_slop_cases():
    """Premise: the spec states the verified Slice is 12 legit / 4 slop;
    every committed case carries verified=true.
    References: eval_corpus.CorpusCase.label, eval_corpus.CorpusCase.verified.
    """
    cases = load_corpus(CORPUS)
    legit = [c for c in cases if c.label == "legit"]
    slop = [c for c in cases if c.label == "slop"]
    assert len(legit) == 12, f"Expected 12 legit, got {len(legit)}"
    assert len(slop) == 4, f"Expected 4 slop, got {len(slop)}"


def test_all_committed_cases_are_verified():
    """Premise: the spec says the human-verified Corpus is moved into the
    repo; unverified cases are funnel volume that should not be committed here.
    References: eval_corpus.CorpusCase.verified, slice_verified in eval_replay.
    """
    cases = load_corpus(CORPUS)
    unverified = [c for c in cases if not c.verified]
    assert not unverified, "Unverified cases found in committed corpus: " + ", ".join(
        f"{c.source.repo}#{c.source.number}" for c in unverified
    )


def test_slop_cases_have_correct_slop_kind_distribution():
    """Premise: the backup shows 1 obvious and 3 subtle slop cases.
    References: eval_corpus.CorpusCase.slop_kind.
    """
    cases = load_corpus(CORPUS)
    slop = [c for c in cases if c.label == "slop"]
    obvious = [c for c in slop if c.slop_kind == "obvious"]
    subtle = [c for c in slop if c.slop_kind == "subtle"]
    assert len(obvious) == 1, f"Expected 1 obvious slop, got {len(obvious)}"
    assert len(subtle) == 3, f"Expected 3 subtle slop, got {len(subtle)}"


# ---------------------------------------------------------------------------
# Language coverage
# ---------------------------------------------------------------------------


def test_legit_cases_are_python_and_slop_cases_are_typescript():
    """Premise: the 12 legit cases are from psf/requests and pallets/flask
    (Python repos); the 4 slop cases are from openclaw/openclaw (TypeScript).
    References: eval_corpus.CorpusCase.language.
    """
    cases = load_corpus(CORPUS)
    legit_langs = {c.language for c in cases if c.label == "legit"}
    slop_langs = {c.language for c in cases if c.label == "slop"}
    assert legit_langs == {"python"}, f"Unexpected legit languages: {legit_langs}"
    assert slop_langs == {"typescript"}, f"Unexpected slop languages: {slop_langs}"


# ---------------------------------------------------------------------------
# Snapshot presence and resolvability
# ---------------------------------------------------------------------------


def test_snapshots_directory_exists_under_corpus():
    """Premise: the eval_scoring._reader_from_snapshot function resolves a
    case's snapshot field relative to the case file's own directory, so
    snapshots must live under CORPUS/snapshots/.
    References: eval_scoring._reader_from_snapshot, eval_corpus.CorpusCase.snapshot.
    """
    assert SNAPSHOTS.is_dir(), f"snapshots/ directory missing under {CORPUS}"


def test_all_referenced_snapshot_files_are_present():
    """Premise: each case's snapshot field is a relative path of the form
    'snapshots/<hash>.json' and is resolved by _reader_from_snapshot relative
    to the case file's directory (CORPUS).
    References: eval_scoring._reader_from_snapshot, CorpusCase.snapshot.
    """
    cases = load_corpus(CORPUS)
    for case in cases:
        snapshot_path = CORPUS / case.snapshot
        assert snapshot_path.is_file(), (
            f"Snapshot file missing for {case.source.repo}#{case.source.number}: {snapshot_path}"
        )


def test_snapshot_files_use_content_hash_filenames():
    """Premise: snapshot filenames are the SHA-256 content hash of the
    snapshot JSON, so a file present under the hash name is the correct one.
    The four expected hashes are derived from the backup.
    References: CORPUS/snapshots/, CorpusCase.snapshot.
    """
    present = {f.name for f in SNAPSHOTS.glob("*.json")}
    missing = EXPECTED_SNAPSHOT_FILENAMES - present
    assert not missing, f"Expected snapshot files not present: {missing}"


def test_every_snapshot_is_resolvable_as_valid_json_with_required_keys():
    """Premise: eval_scoring._reader_from_snapshot reads the snapshot JSON
    and accesses keys 'symbols', 'paths', and 'unparsed_exts'; a snapshot
    missing any of these would silently fail to hydrate a RepoReader.
    References: eval_scoring._reader_from_snapshot, RepoReader.from_index.
    """
    cases = load_corpus(CORPUS)
    for case in cases:
        snapshot_path = CORPUS / case.snapshot
        data = json.loads(snapshot_path.read_text())
        for key in ("symbols", "paths", "unparsed_exts"):
            assert key in data, (
                f"Snapshot for {case.source.repo}#{case.source.number} "
                f"missing key '{key}': {snapshot_path}"
            )


# ---------------------------------------------------------------------------
# Preserved metadata: linked_issues, slop_kind, verified flags
# ---------------------------------------------------------------------------


def test_openclaw_90081_carries_linked_issues_entry():
    """Premise: the backup for openclaw/openclaw#90081 carries a non-empty
    linked_issues list in its event; this must be preserved exactly.
    References: eval_corpus.CorpusCase.event['linked_issues'].
    """
    cases = load_corpus(CORPUS)
    target = next(
        (c for c in cases if c.source.repo == "openclaw/openclaw" and c.source.number == 90081),
        None,
    )
    assert target is not None, "openclaw/openclaw#90081 not found in corpus"
    linked = target.event.get("linked_issues")
    assert linked and len(linked) >= 1, (
        f"openclaw/openclaw#90081 should carry at least one linked_issue, got: {linked}"
    )
    # The linked issue number from the backup is 89278.
    numbers = [li["number"] for li in linked]
    assert 89278 in numbers, f"Expected linked issue 89278, found: {numbers}"


def test_slop_kind_is_preserved_on_all_slop_cases():
    """Premise: the backup carries slop_kind for every slop case; migrating
    the files must not strip or null out this field.
    References: eval_corpus.CorpusCase.slop_kind.
    """
    cases = load_corpus(CORPUS)
    for case in (c for c in cases if c.label == "slop"):
        assert case.slop_kind in ("obvious", "subtle"), (
            f"{case.source.repo}#{case.source.number} is slop but slop_kind is {case.slop_kind!r}"
        )


def test_legit_cases_have_null_slop_kind():
    """Premise: parse_case raises CorpusError when slop_kind is non-null on a
    legit case, so any such case would already fail the loader. This test
    states the positive invariant explicitly.
    References: eval_corpus.CorpusCase.slop_kind, eval_corpus.parse_case.
    """
    cases = load_corpus(CORPUS)
    for case in (c for c in cases if c.label == "legit"):
        assert case.slop_kind is None, (
            f"Legit case {case.source.repo}#{case.source.number} has slop_kind {case.slop_kind!r}"
        )


# ---------------------------------------------------------------------------
# Dropped cases are excluded from the glob but present on disk
# ---------------------------------------------------------------------------


def test_dropped_directory_exists_under_corpus():
    """Premise: the spec says the 6 dropped cases are kept for record under
    CORPUS/dropped/ but must not be globbed as cases (they are in a subdir,
    not CORPUS/*.json).
    References: eval_corpus.load_corpus (globs CORPUS/*.json only).
    """
    assert DROPPED.is_dir(), f"dropped/ directory missing under {CORPUS}"


def test_dropped_cases_are_present_under_dropped_not_at_corpus_root():
    """Premise: the 6 dropped case filenames are known from the backup; they
    must sit under CORPUS/dropped/ so load_corpus never globs them.
    References: eval_corpus.load_corpus, CORPUS/dropped/.
    """
    for filename in DROPPED_FILENAMES:
        dropped_path = DROPPED / filename
        corpus_root_path = CORPUS / filename
        assert dropped_path.is_file(), f"Dropped case missing from dropped/: {filename}"
        assert not corpus_root_path.exists(), (
            f"Dropped case {filename} found at corpus root — it will be globbed as a case"
        )


def test_load_corpus_does_not_load_dropped_cases():
    """Premise: load_corpus globs only CORPUS/*.json; dropped cases live in
    CORPUS/dropped/*.json, so they are invisible to the loader.
    References: eval_corpus.load_corpus (glob pattern).
    """
    cases = load_corpus(CORPUS)
    loaded_numbers = {c.source.number for c in cases}
    # numbers of the 6 dropped openclaw cases
    dropped_numbers = {90087, 90127, 90137, 90189, 90225, 90253}
    overlap = loaded_numbers & dropped_numbers
    assert not overlap, f"Dropped case numbers appeared in the loaded corpus: {overlap}"


# ---------------------------------------------------------------------------
# Known rates over the verified Slice (model-free)
# ---------------------------------------------------------------------------


def test_slice_counts_match_known_result():
    """Premise: all 16 committed cases carry verified=true, so the verified
    Slice equals the full corpus; the known result from the spec is 12 legit /
    4 slop with hard FPR 0% (no legit case is labelled slop).
    References: eval_replay.slice_verified, eval_corpus.load_corpus.
    """
    from eval_replay import slice_verified

    cases = load_corpus(CORPUS)

    # Simulate the minimal record shape slice_verified reads.
    records = [
        {
            "name": f"{c.source.repo}#{c.source.number}",
            "verified": c.verified,
            "expected_positive": c.label == "slop",
            "label": c.label,
        }
        for c in cases
    ]
    sl = slice_verified(records)
    assert len(sl) == 16, f"Expected 16 in Slice (all verified), got {len(sl)}"

    legit_in_slice = [r for r in sl if not r["expected_positive"]]
    slop_in_slice = [r for r in sl if r["expected_positive"]]
    assert len(legit_in_slice) == 12
    assert len(slop_in_slice) == 4
