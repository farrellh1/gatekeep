from __future__ import annotations

import re

from brain.schemas import NormalizedEvent, Finding
from brain.repo_reader import RepoReader

_CODE_SPAN_RE = re.compile(r"`+([^`]+?)`+")
_CALL_REF_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\(")


def cited_symbols(text: str) -> set[str]:
    cited: set[str] = set()
    for span in _CODE_SPAN_RE.findall(text):
        for ref in _CALL_REF_RE.findall(span):
            cited.add(ref.split(".")[-1])
    return cited


def cited_symbols_exist(event: NormalizedEvent) -> Finding:
    reader = RepoReader(event.repo.clone_path)
    cited = cited_symbols(f"{event.title}\n{event.body}")
    if not cited:
        return Finding(
            check="cited_symbols_exist", result="pass",
            evidence="no specific code symbols cited",
        )
    missing = [name for name in cited if not reader.symbol_exists(name)]
    if missing:
        names = ", ".join(f"`{m}()`" for m in sorted(missing))
        return Finding(
            check="cited_symbols_exist", result="fail",
            evidence=f"references {names} which do not exist in this repo",
        )
    return Finding(
        check="cited_symbols_exist", result="pass",
        evidence="all cited symbols exist in the repo",
    )


def touches_real_files(event: NormalizedEvent) -> Finding:
    if not event.changed_files:
        return Finding(check="touches_real_files", result="unknown", evidence="no file list")
    reader = RepoReader(event.repo.clone_path)
    missing = [f for f in event.changed_files if not reader.file_exists(f)]
    if missing and len(missing) == len(event.changed_files):
        return Finding(
            check="touches_real_files", result="fail",
            evidence=f"changed paths do not exist and are not added: {missing}",
        )
    return Finding(check="touches_real_files", result="pass", evidence="changed paths coherent")


def cosmetic_only(event: NormalizedEvent) -> Finding:
    if not event.diff:
        return Finding(check="cosmetic_only", result="unknown", evidence="no diff")
    changed = [
        l for l in event.diff.splitlines()
        if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))
    ]

    def norm(line: str) -> str:
        return re.sub(r"\s+", "", line[1:])

    adds = {norm(l) for l in changed if l.startswith("+")}
    dels = {norm(l) for l in changed if l.startswith("-")}
    if adds == dels and adds:
        return Finding(
            check="cosmetic_only", result="fail",
            evidence="diff is whitespace/formatting only -- no behavior change",
        )
    return Finding(check="cosmetic_only", result="pass", evidence="diff changes behavior")


def ci_status_check(event: NormalizedEvent) -> Finding:
    if event.ci_status in (None, "none", "pending"):
        return Finding(
            check="ci_status", result="unknown",
            evidence=f"CI {event.ci_status or 'absent'}",
        )
    if event.ci_status == "failure":
        return Finding(check="ci_status", result="fail", evidence="existing CI is failing")
    return Finding(check="ci_status", result="pass", evidence="existing CI passing")
