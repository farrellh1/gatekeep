from __future__ import annotations

from core.repo_reader import RepoReader
from core.schemas import NormalizedEvent


class RunContext:
    """Per-event scratch shared across a Suite's checks.

    Carries one lazily-built `RepoReader` for the event's clone, built on first
    access and reused thereafter. Sharing it means the tree-sitter index is built
    once per event instead of once per reader-using check.
    """

    def __init__(self, event: NormalizedEvent):
        self.event = event
        self._reader: RepoReader | None = None

    @property
    def reader(self) -> RepoReader:
        if self._reader is None:
            self._reader = RepoReader(self.event.repo.clone_path)
        return self._reader
