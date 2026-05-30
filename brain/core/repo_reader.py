from __future__ import annotations

import re
from pathlib import Path

_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}


class RepoReader:
    """Reads a local clone. Filesystem only -- never touches GitHub."""

    def __init__(self, clone_path: str):
        self.root = Path(clone_path)

    def file_exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).is_file()

    def _code_files(self):
        for p in self.root.rglob("*"):
            if p.is_file() and p.suffix in _CODE_EXT and ".git" not in p.parts:
                yield p

    def symbol_exists(self, name: str) -> bool:
        """True if `name` appears as an identifier anywhere in the code.

        Word-boundary match avoids substring false positives. v1 heuristic:
        good enough to catch hallucinated names that appear nowhere. A later
        refinement is per-language AST parsing to confirm a real definition.
        """
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        for p in self._code_files():
            try:
                if pattern.search(p.read_text(errors="ignore")):
                    return True
            except OSError:
                continue
        return False
