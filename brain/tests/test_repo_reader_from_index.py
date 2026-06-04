"""Tests for RepoReader.from_index -- the snapshot-backed construction path.

from_index(symbols, paths, unparsed_exts) returns a fully functional RepoReader
without a clone on disk; these tests pin that it answers symbol_exists,
file_exists, and evidence_grade from the frozen sets alone and stays in parity
with a clone-backed reader built from the same fixture.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.repo_reader import RepoReader

# The real fixture clone used by the existing clone-backed tests so parity tests
# can compare hydrated vs. clone-backed answers on the same repo.
CLONE = str(Path(__file__).parent / "fixtures" / "clone")

# Ground-truth sets derived from the fixture clone:
#   src/auth.py  — Python, fully parseable by tree-sitter
#   src/server.go — Go, fully parseable by tree-sitter
# All identifiers visible in the fixture (see fixtures/clone/src/auth.py and
# fixtures/clone/src/server.go): login, helper_check, os, user, password,
# StartServer, and internal tree-sitter tokens such as 'main'.
_FIXTURE_SYMBOLS = frozenset({"login", "helper_check", "os", "StartServer", "main"})
_FIXTURE_PATHS = frozenset({"src/auth.py", "src/server.go"})
_FIXTURE_UNPARSED_EXTS: frozenset[str] = frozenset()  # fixture has no unparseable files


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_from_index_returns_repo_reader_instance():
    # from_index must return a RepoReader (or subclass) so callers can use the
    # same interface regardless of whether the reader was built from a clone or
    # from a snapshot.
    reader = RepoReader.from_index(_FIXTURE_SYMBOLS, _FIXTURE_PATHS, _FIXTURE_UNPARSED_EXTS)
    assert isinstance(reader, RepoReader)


def test_from_index_accepts_plain_iterables():
    # Callers may pass lists, sets, or any iterable — not just frozensets.
    reader = RepoReader.from_index(
        list(_FIXTURE_SYMBOLS),
        list(_FIXTURE_PATHS),
        list(_FIXTURE_UNPARSED_EXTS),
    )
    assert isinstance(reader, RepoReader)


# ---------------------------------------------------------------------------
# symbol_exists — reads from the frozen symbols set, never calls _build_index
# ---------------------------------------------------------------------------


def test_symbol_exists_true_from_frozen_symbols():
    # A symbol that is in the supplied set must be found.
    reader = RepoReader.from_index({"login", "helper_check"}, set(), set())
    assert reader.symbol_exists("login") is True


def test_symbol_exists_false_from_frozen_symbols():
    # A symbol absent from the supplied set must not be found.
    reader = RepoReader.from_index({"login"}, set(), set())
    assert reader.symbol_exists("validateToken") is False


def test_symbol_exists_does_not_call_build_index():
    # _build_index walks the filesystem; a hydrated reader must never call it,
    # because there is no clone on disk.
    reader = RepoReader.from_index({"login"}, set(), set())
    with patch.object(reader, "_build_index", side_effect=AssertionError("_build_index called")):
        # Calling symbol_exists must not trigger _build_index even on first call.
        result = reader.symbol_exists("login")
    assert result is True


def test_symbol_exists_does_not_call_build_index_on_miss():
    # _build_index must not be called even when the symbol is absent.
    reader = RepoReader.from_index({"login"}, set(), set())
    with patch.object(reader, "_build_index", side_effect=AssertionError("_build_index called")):
        result = reader.symbol_exists("ghost")
    assert result is False


# ---------------------------------------------------------------------------
# file_exists — reads from the frozen paths set, never stats disk
# ---------------------------------------------------------------------------


def test_file_exists_true_for_path_in_frozen_set():
    reader = RepoReader.from_index(set(), {"src/auth.py", "src/server.go"}, set())
    assert reader.file_exists("src/auth.py") is True


def test_file_exists_false_for_path_not_in_frozen_set():
    reader = RepoReader.from_index(set(), {"src/auth.py"}, set())
    assert reader.file_exists("src/missing.py") is False


def test_file_exists_does_not_stat_disk(tmp_path):
    # The hydrated reader must not touch the filesystem at all.  We verify this
    # by giving the reader a path that does NOT exist on disk and confirming the
    # reader still says True (because the path is in the frozen set).
    reader = RepoReader.from_index(set(), {"nonexistent/path.py"}, set())
    # The file definitely does not exist on disk.
    assert not (tmp_path / "nonexistent" / "path.py").exists()
    # But from_index was told it exists.
    assert reader.file_exists("nonexistent/path.py") is True


def test_file_exists_does_not_confirm_disk_file_not_in_set():
    # Conversely, a file that IS on disk but NOT in the frozen set must return
    # False — the hydrated reader is authoritative from the snapshot, not disk.
    # src/auth.py is present in the real fixture clone on disk.
    reader = RepoReader.from_index(set(), set(), set())  # empty paths set
    assert reader.file_exists("src/auth.py") is False


# ---------------------------------------------------------------------------
# evidence_grade — uses the frozen _unparsed_exts, no lazy build_index call
# ---------------------------------------------------------------------------


def test_evidence_grade_high_when_unparsed_exts_empty():
    # An empty unparsed_exts means all code was fully parsed: HIGH confidence.
    reader = RepoReader.from_index({"login"}, {"src/auth.py"}, set())
    assert reader.evidence_grade() == ("HIGH", "AST_TREE_SITTER")


def test_evidence_grade_low_when_unparsed_exts_nonempty():
    reader = RepoReader.from_index({"login"}, {"src/auth.py"}, {".php"})
    assert reader.evidence_grade() == ("LOW", "HEURISTIC")


def test_evidence_grade_high_when_relevant_exts_disjoint_from_unparsed():
    # A stray unparseable extension (.sh) must not downgrade a miss in Python
    # when the caller specifies relevant_exts={".py"}.
    reader = RepoReader.from_index({"login"}, {"src/auth.py"}, {".sh"})
    assert reader.evidence_grade({".py"}) == ("HIGH", "AST_TREE_SITTER")


def test_evidence_grade_low_when_relevant_exts_overlap_unparsed():
    reader = RepoReader.from_index({"login"}, {"src/auth.py"}, {".sh"})
    assert reader.evidence_grade({".sh"}) == ("LOW", "HEURISTIC")


def test_evidence_grade_does_not_call_build_index():
    # evidence_grade on a hydrated reader must never trigger filesystem access.
    reader = RepoReader.from_index({"login"}, {"src/auth.py"}, set())
    with patch.object(reader, "_build_index", side_effect=AssertionError("_build_index called")):
        result = reader.evidence_grade()
    assert result == ("HIGH", "AST_TREE_SITTER")


# ---------------------------------------------------------------------------
# Parity — hydrated reader gives the same answers as the clone-backed reader
# ---------------------------------------------------------------------------


def _hydrate_from_clone(clone_path: str) -> RepoReader:
    """Build a clone-backed reader, let it index, then re-hydrate from its sets.

    This is the contract a JSON snapshot loader would implement: harvest the
    three sets from a real clone, then construct a from_index reader from them.
    """
    clone_reader = RepoReader(clone_path)
    # Force the index so _symbols and _unparsed_exts are populated.
    clone_reader._symbols = clone_reader._build_index()
    symbols = frozenset(clone_reader._symbols)
    unparsed_exts = frozenset(clone_reader._unparsed_exts)
    # Build the paths set by walking the fixture clone.
    fixture = Path(clone_path)
    paths = frozenset(
        str(p.relative_to(fixture))
        for p in fixture.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )
    return RepoReader.from_index(symbols, paths, unparsed_exts)


def test_parity_symbol_exists_true():
    # A hydrated reader built from the fixture clone's snapshot must agree with
    # a clone-backed reader on every symbol_exists query.
    clone_reader = RepoReader(CLONE)
    hydrated = _hydrate_from_clone(CLONE)
    for name in ("login", "helper_check", "StartServer"):
        assert hydrated.symbol_exists(name) == clone_reader.symbol_exists(name), (
            f"symbol_exists('{name}') diverged between clone-backed and hydrated readers"
        )


def test_parity_symbol_exists_false():
    clone_reader = RepoReader(CLONE)
    hydrated = _hydrate_from_clone(CLONE)
    for name in ("validateToken", "ghost_comment", "ghost_string"):
        assert hydrated.symbol_exists(name) == clone_reader.symbol_exists(name), (
            f"symbol_exists('{name}') diverged between clone-backed and hydrated readers"
        )


def test_parity_file_exists():
    clone_reader = RepoReader(CLONE)
    hydrated = _hydrate_from_clone(CLONE)
    assert hydrated.file_exists("src/auth.py") == clone_reader.file_exists("src/auth.py")
    assert hydrated.file_exists("src/server.go") == clone_reader.file_exists("src/server.go")
    assert hydrated.file_exists("src/missing.py") == clone_reader.file_exists("src/missing.py")


def test_parity_evidence_grade_no_relevant_exts():
    clone_reader = RepoReader(CLONE)
    hydrated = _hydrate_from_clone(CLONE)
    assert hydrated.evidence_grade() == clone_reader.evidence_grade()


def test_parity_evidence_grade_with_python_ext():
    clone_reader = RepoReader(CLONE)
    hydrated = _hydrate_from_clone(CLONE)
    assert hydrated.evidence_grade({".py"}) == clone_reader.evidence_grade({".py"})


def test_parity_evidence_grade_with_go_ext():
    clone_reader = RepoReader(CLONE)
    hydrated = _hydrate_from_clone(CLONE)
    assert hydrated.evidence_grade({".go"}) == clone_reader.evidence_grade({".go"})


# ---------------------------------------------------------------------------
# Clone-backed path is unchanged — from_index must not affect existing behavior
# ---------------------------------------------------------------------------


def test_clone_backed_symbol_exists_still_works():
    # The snapshot path is additive: a clone-backed reader still indexes from disk.
    r = RepoReader(CLONE)
    assert r.symbol_exists("login") is True
    assert r.symbol_exists("validateToken") is False


def test_clone_backed_file_exists_still_works():
    r = RepoReader(CLONE)
    assert r.file_exists("src/auth.py") is True
    assert r.file_exists("src/missing.py") is False


def test_clone_backed_evidence_grade_still_works():
    r = RepoReader(CLONE)
    assert r.evidence_grade() == ("HIGH", "AST_TREE_SITTER")
