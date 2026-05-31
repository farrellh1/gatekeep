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
