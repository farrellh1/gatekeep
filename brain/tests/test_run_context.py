from pathlib import Path

from core.repo_reader import RepoReader
from core.run_context import RunContext
from core.schemas import NormalizedEvent

CLONE = str(Path(__file__).parent / "fixtures" / "clone")


def _ev(**kw):
    base = dict(
        delivery_id="d",
        kind="pull_request",
        action="opened",
        repo={"owner": "o", "name": "r", "default_branch": "main", "clone_path": CLONE},
        number=1,
        title="t",
        body="b",
        author={"login": "a", "account_age_days": 10, "is_first_time_contributor": True},
    )
    base.update(kw)
    return NormalizedEvent(**base)


def test_reader_is_lazy_and_shared():
    # the reader is built once, on first access, then reused
    ctx = RunContext(_ev())
    first = ctx.reader
    assert isinstance(first, RepoReader)
    assert ctx.reader is first  # same instance every access


def test_reader_built_from_event_clone_path():
    ctx = RunContext(_ev())
    assert str(ctx.reader.root) == CLONE


def test_injected_reader_is_used_instead_of_the_clone():
    # An injected reader (a snapshot-hydrated one) is returned as-is and the event's
    # clone_path is never read -- this is the seam the Corpus eval scores cases on.
    injected = RepoReader.from_index(symbols=["login"], paths=["src/auth.py"], unparsed_exts=[])
    ctx = RunContext(_ev(), reader=injected)
    assert ctx.reader is injected
    assert ctx.reader.symbol_exists("login") is True
    assert ctx.reader.file_exists("src/auth.py") is True
