from __future__ import annotations

from core.repo_reader import RepoReader
from core.schemas import NormalizedEvent


class RunContext:
    """Per-event scratch shared across a Suite's checks.

    Carries one `RepoReader` for the event, shared across the Suite's checks so the
    tree-sitter index is built once. By default the reader is built lazily from the
    event's clone on first access. A caller may instead inject a pre-built reader --
    a snapshot-hydrated `RepoReader.from_index` -- so the same checks can run against
    a frozen index with no clone on disk, which is how the Corpus eval scores cases.
    """

    def __init__(self, event: NormalizedEvent, reader: RepoReader | None = None):
        self.event = event
        self._reader: RepoReader | None = reader

    @property
    def reader(self) -> RepoReader:
        if self._reader is None:
            self._reader = RepoReader(self.event.repo.clone_path)
        return self._reader
