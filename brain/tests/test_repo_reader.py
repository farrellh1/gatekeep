from pathlib import Path

from core.repo_reader import RepoReader

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def test_symbol_exists_true():
    r = RepoReader(CLONE)
    assert r.symbol_exists("login") is True


def test_symbol_exists_false():
    r = RepoReader(CLONE)
    assert r.symbol_exists("validateToken") is False


def test_symbol_in_comment_does_not_count():
    r = RepoReader(CLONE)
    assert r.symbol_exists("ghost_comment") is False


def test_symbol_in_string_does_not_count():
    r = RepoReader(CLONE)
    assert r.symbol_exists("ghost_string") is False


def test_referenced_symbol_counts():
    r = RepoReader(CLONE)
    assert r.symbol_exists("helper_check") is True


def test_symbol_exists_across_languages():
    r = RepoReader(CLONE)
    assert r.symbol_exists("StartServer") is True


def test_file_exists():
    r = RepoReader(CLONE)
    assert r.file_exists("src/auth.py") is True
    assert r.file_exists("src/missing.py") is False


def test_evidence_grade_high_when_fully_parseable():
    r = RepoReader(CLONE)
    confidence, engine = r.evidence_grade()
    assert (confidence, engine) == ("HIGH", "AST_TREE_SITTER")


def test_evidence_grade_low_when_unparseable_language_present(tmp_path):
    # no language context -> repo-wide grading: any unparseable file -> LOW
    (tmp_path / "app.py").write_text("def handler():\n    return 1\n")
    (tmp_path / "legacy.php").write_text("<?php function legacy() {} ?>\n")
    r = RepoReader(str(tmp_path))
    assert r.evidence_grade() == ("LOW", "HEURISTIC")


def test_evidence_grade_high_for_parsed_language_despite_unrelated_unparseable(tmp_path):
    # a stray shell script must NOT downgrade a miss in fully-parsed Python
    (tmp_path / "app.py").write_text("def handler():\n    return 1\n")
    (tmp_path / "deploy.sh").write_text("#!/bin/sh\necho hi\n")
    r = RepoReader(str(tmp_path))
    assert r.evidence_grade({".py"}) == ("HIGH", "AST_TREE_SITTER")
    assert r.evidence_grade({".sh"}) == ("LOW", "HEURISTIC")  # the symbol's own lang is unseen


def test_unparseable_language_makes_a_miss_low_confidence(tmp_path):
    (tmp_path / "legacy.php").write_text("<?php function realThing() {} ?>\n")
    r = RepoReader(str(tmp_path))
    assert r.symbol_exists("realThing") is False  # blind spot: we can't see into .php
    assert r.evidence_grade() == ("LOW", "HEURISTIC")
