from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from brain.schemas import NormalizedEvent, Finding
from brain.repo_reader import RepoReader
from brain.llm import llm

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


class _DiffMatch(BaseModel):
    mismatch: bool
    reason: str


def diff_matches_description(event: NormalizedEvent) -> Finding:
    if not event.diff:
        return Finding(check="diff_matches_description", result="unknown", evidence="no diff")
    msg = [
        {"role": "system", "content":
            "You compare a PR description to its actual diff. Set mismatch=true ONLY if the "
            "diff plainly does not do what the description claims (e.g. claims a fix but is a "
            "no-op/comment/unrelated change). Be conservative."},
        {"role": "user", "content":
            f"DESCRIPTION:\n{event.title}\n{event.body}\n\nDIFF:\n{event.diff}"},
    ]
    out = llm(msg, schema=_DiffMatch)
    return Finding(
        check="diff_matches_description",
        result="fail" if out.mismatch else "pass", evidence=out.reason,
    )


class _Repro(BaseModel):
    has_repro: bool
    reason: str


def has_repro(event: NormalizedEvent) -> Finding:
    msg = [
        {"role": "system", "content":
            "Does this bug report contain a concrete reproduction (steps, code, or a stack "
            "trace)? has_repro=false only if it is vague with no way to reproduce."},
        {"role": "user", "content": f"{event.title}\n{event.body}"},
    ]
    out = llm(msg, schema=_Repro)
    return Finding(
        check="has_repro",
        result="pass" if out.has_repro else "fail", evidence=out.reason,
    )


class _Dupe(BaseModel):
    duplicate_of: Optional[int]
    reason: str


def is_duplicate(event: NormalizedEvent) -> Finding:
    candidates = event.existing_issues or []
    if not candidates:
        return Finding(check="is_duplicate", result="unknown", evidence="no candidates provided")
    listing = "\n".join(f"#{c.number}: {c.title}" for c in candidates)
    msg = [
        {"role": "system", "content":
            "Is the NEW issue a semantic duplicate of one of the EXISTING issues? "
            "Return the duplicate issue number or null. Be conservative."},
        {"role": "user", "content":
            f"NEW:\n{event.title}\n{event.body}\n\nEXISTING:\n{listing}"},
    ]
    out = llm(msg, schema=_Dupe)
    if out.duplicate_of is not None:
        return Finding(
            check="is_duplicate", result="fail",
            evidence=f"appears to duplicate #{out.duplicate_of}: {out.reason}",
        )
    return Finding(check="is_duplicate", result="pass", evidence="no duplicate found")
