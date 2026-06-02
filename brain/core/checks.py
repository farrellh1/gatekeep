from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from core.llm import llm
from core.run_context import RunContext
from core.schemas import CheckResult, NormalizedEvent

_CODE_SPAN_RE = re.compile(r"`+([^`]+?)`+")
_CALL_REF_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\(")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def cited_symbols(text: str) -> set[str]:
    cited: set[str] = set()
    for span in _CODE_SPAN_RE.findall(text):
        for ref in _CALL_REF_RE.findall(span):
            cited.add(ref.split(".")[-1])
    return cited


def diff_added_identifiers(diff: str | None) -> set[str]:
    """Identifiers on the diff's added lines.

    The brain clones only the base branch, so a symbol a PR is adding isn't in the
    index yet; counting it as present avoids flagging code being written. Scans
    added lines wholesale (comments and strings included), erring toward not flagging.
    """
    if not diff:
        return set()
    added = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return set(_IDENT_RE.findall(added))


def diff_added_files(diff: str | None) -> set[str]:
    """Paths the diff introduces as brand-new files.

    The brain clones only the base branch, so a file a PR adds isn't on disk yet;
    counting it as present avoids flagging a greenfield contribution. A new file
    shows its old side as /dev/null, so the following `+++ b/<path>` header names a
    path the PR creates -- a diff that instead claims to modify a path absent from
    the clone keeps its real old side and is left to fail as a hallucination.
    """
    if not diff:
        return set()
    added: set[str] = set()
    prev = ""
    for line in diff.splitlines():
        if line.startswith("+++ ") and prev.startswith("--- /dev/null"):
            path = line[4:]
            added.add(path[2:] if path.startswith("b/") else path)
        prev = line
    return added


def diff_renamed_paths(diff: str | None) -> set[str]:
    """Destination paths the diff renames a file to.

    A rename moves an existing file, so its new path is absent from the base clone
    just like an added file -- but the diff shows no /dev/null, only a `rename to
    <path>` header. Counting that target as present keeps a rename PR from reading
    as touching nothing real.
    """
    if not diff:
        return set()
    marker = "rename to "
    return {line[len(marker) :] for line in diff.splitlines() if line.startswith(marker)}


def cited_symbols_exist(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    reader = ctx.reader
    cited = cited_symbols(f"{event.title}\n{event.body}")
    if not cited:
        return CheckResult(result="pass", evidence="no specific code symbols cited")
    added = diff_added_identifiers(event.diff)
    missing = [name for name in cited if not reader.symbol_exists(name) and name not in added]
    # grade confidence against the languages the change touches
    relevant = {Path(f).suffix for f in (event.changed_files or [])}
    confidence, engine = reader.evidence_grade(relevant or None)
    if missing:
        names = ", ".join(f"`{m}()`" for m in sorted(missing))
        return CheckResult(
            result="fail",
            evidence=f"references {names} which do not exist in this repo",
            confidence=confidence,
            engine=engine,
        )
    return CheckResult(
        result="pass",
        evidence="all cited symbols exist in the repo",
        confidence="HIGH",
        engine=engine,
    )


# the gateway's sentinel for a diff it capped at GATEKEEP_MAX_DIFF_BYTES
_DIFF_TRUNCATED = "[gatekeep: diff truncated"


def _diff_vouches(diff: str | None) -> bool:
    """Whether the diff is complete enough to call an unseen path hallucinated.

    The gateway sends no diff for some events and truncates oversized ones. A null
    or truncated diff cannot distinguish a file the PR adds from one it invents --
    the clone has neither and the file's header may simply be absent or past the
    cut -- so the check must abstain rather than fail.
    """
    return bool(diff) and _DIFF_TRUNCATED not in diff


def touches_real_files(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if not event.changed_files:
        return CheckResult(result="unknown", evidence="no file list")
    reader = ctx.reader
    present = diff_added_files(event.diff) | diff_renamed_paths(event.diff)
    missing = [f for f in event.changed_files if not reader.file_exists(f) and f not in present]
    if missing and len(missing) == len(event.changed_files):
        if not _diff_vouches(event.diff):
            return CheckResult(
                result="unknown",
                evidence=f"changed paths absent from the base clone, no usable diff to "
                f"confirm they are added: {missing}",
            )
        return CheckResult(
            result="fail",
            evidence=f"changed paths do not exist and are not added: {missing}",
        )
    return CheckResult(result="pass", evidence="changed paths coherent")


def cosmetic_only(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if not event.diff:
        return CheckResult(result="unknown", evidence="no diff")
    changed = [
        line
        for line in event.diff.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    def norm(line: str) -> str:
        return re.sub(r"\s+", "", line[1:])

    adds = {norm(line) for line in changed if line.startswith("+")}
    dels = {norm(line) for line in changed if line.startswith("-")}
    if adds == dels and adds:
        return CheckResult(
            result="fail",
            evidence="diff is whitespace/formatting only -- no behavior change",
        )
    return CheckResult(result="pass", evidence="diff changes behavior")


def ci_status_check(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if event.ci_status in (None, "none", "pending"):
        return CheckResult(result="unknown", evidence=f"CI {event.ci_status or 'absent'}")
    if event.ci_status == "failure":
        return CheckResult(result="fail", evidence="existing CI is failing")
    return CheckResult(result="pass", evidence="existing CI passing")


class _DiffMatch(BaseModel):
    mismatch: bool = False  # default to "matches" if the field is absent
    reason: str = ""


def diff_matches_description(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if not event.diff:
        return CheckResult(result="unknown", evidence="no diff")
    msg = [
        {
            "role": "system",
            "content": "You compare a PR description to its actual diff. Set "
            "mismatch=true ONLY if the "
            "diff plainly does not do what the description claims (e.g. claims a fix but is a "
            "no-op/comment/unrelated change). Be conservative.",
        },
        {
            "role": "user",
            "content": f"DESCRIPTION:\n{event.title}\n{event.body}\n\nDIFF:\n{event.diff}",
        },
    ]
    out = llm(msg, schema=_DiffMatch, role="checks")
    return CheckResult(
        result="fail" if out.mismatch else "pass",
        evidence=out.reason,
        engine="LLM",
    )


class _Repro(BaseModel):
    has_repro: bool = True  # default to "has repro" if the field is absent
    reason: str = ""


def has_repro(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    msg = [
        {
            "role": "system",
            "content": "Does this bug report contain a concrete reproduction (steps, "
            "code, or a stack "
            "trace)? has_repro=false only if it is vague with no way to reproduce.",
        },
        {"role": "user", "content": f"{event.title}\n{event.body}"},
    ]
    out = llm(msg, schema=_Repro, role="checks")
    return CheckResult(
        result="pass" if out.has_repro else "fail",
        evidence=out.reason,
        engine="LLM",
    )


class _Dupe(BaseModel):
    duplicate_of: int | None = None  # default to "not a duplicate" if absent
    reason: str = ""


def is_duplicate(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    candidates = event.existing_issues or []
    if not candidates:
        return CheckResult(result="unknown", evidence="no candidates provided")
    listing = "\n".join(f"#{c.number}: {c.title}" for c in candidates)
    msg = [
        {
            "role": "system",
            "content": "Is the NEW issue a semantic duplicate of one of the EXISTING issues? "
            "Return the duplicate issue number or null. Be conservative.",
        },
        {"role": "user", "content": f"NEW:\n{event.title}\n{event.body}\n\nEXISTING:\n{listing}"},
    ]
    out = llm(msg, schema=_Dupe, role="checks")
    if out.duplicate_of is not None:
        return CheckResult(
            result="fail",
            evidence=f"appears to duplicate #{out.duplicate_of}: {out.reason}",
            engine="LLM",
        )
    return CheckResult(result="pass", evidence="no duplicate found", engine="LLM")
