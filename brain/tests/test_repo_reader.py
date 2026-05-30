from pathlib import Path

from brain.repo_reader import RepoReader

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def test_symbol_exists_true():
    r = RepoReader(CLONE)
    assert r.symbol_exists("login") is True


def test_symbol_exists_false():
    r = RepoReader(CLONE)
    assert r.symbol_exists("validateToken") is False


def test_file_exists():
    r = RepoReader(CLONE)
    assert r.file_exists("src/auth.py") is True
    assert r.file_exists("src/missing.py") is False
