from __future__ import annotations

import re
from pathlib import Path

from tree_sitter_language_pack import get_parser

_LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
}


def _val(node, name):
    """Read a tree-sitter Node member, calling it if the binding exposes it
    as a method rather than a property (this build does both)."""
    attr = getattr(node, name)
    return attr() if callable(attr) else attr


class RepoReader:
    """Reads a local clone. Filesystem only -- never touches GitHub."""

    def __init__(self, clone_path: str):
        self.root = Path(clone_path)
        self._symbols: set[str] | None = None

    def file_exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).is_file()

    def _code_files(self):
        for p in self.root.rglob("*"):
            if p.is_file() and p.suffix in _LANG_BY_EXT and ".git" not in p.parts:
                yield p

    def symbol_exists(self, name: str) -> bool:
        """True if `name` appears as a real identifier in the code.

        Each file is parsed with tree-sitter and its identifier tokens indexed,
        so a name that only shows up in a comment, a string literal, or as a
        substring of a longer identifier does not count. A definition or any
        genuine reference does -- a real but imported/used symbol still passes,
        which avoids falsely accusing a contributor of hallucinating.
        """
        if self._symbols is None:
            self._symbols = self._build_index()
        return name in self._symbols

    def _build_index(self) -> set[str]:
        symbols: set[str] = set()
        for p in self._code_files():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            tree = self._parse(_LANG_BY_EXT[p.suffix], text)
            if tree is None:
                symbols.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
            else:
                self._collect_identifiers(tree, text.encode("utf-8"), symbols)
        return symbols

    @staticmethod
    def _parse(lang: str, text: str):
        parser = get_parser(lang)
        try:
            return parser.parse(text)
        except Exception:
            return None

    @staticmethod
    def _collect_identifiers(tree, data: bytes, symbols: set[str]) -> None:
        stack = [_val(tree, "root_node")]
        while stack:
            node = stack.pop()
            count = _val(node, "child_count")
            if count == 0:
                kind = _val(node, "kind")
                if isinstance(kind, str) and kind.endswith("identifier"):
                    name = data[_val(node, "start_byte"):_val(node, "end_byte")]
                    symbols.add(name.decode("utf-8", "ignore"))
                continue
            for i in range(count):
                child = node.child(i)
                if child is not None:
                    stack.append(child)
