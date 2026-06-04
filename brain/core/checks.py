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


def _diff_hunks(diff: str) -> list[list[str]]:
    """The changed/context lines of each hunk, kept separate per hunk.

    A file/meta header ends the current hunk; a `@@` or a change line with no open
    hunk starts one. Grouping per hunk keeps a line moved between files (or hunks)
    from cancelling out against its own paste elsewhere.
    """
    hunks: list[list[str]] = []
    cur: list[str] | None = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            cur = []
            hunks.append(cur)
        elif line.startswith(("--- ", "+++ ", "diff --git ")):
            cur = None
        elif line.startswith(("+", "-")):
            if cur is None:
                cur = []
                hunks.append(cur)
            cur.append(line)
        elif cur is not None:
            cur.append(line)
    return hunks


def cosmetic_only(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if not event.diff:
        return CheckResult(result="unknown", evidence="no diff")

    def norm(line: str) -> str:
        # leading indentation is significant (it is the behavior in Python/YAML);
        # only internal and trailing whitespace is cosmetic
        body = line[1:]
        rest = body.lstrip()
        leading = body[: len(body) - len(rest)]
        return leading + re.sub(r"\s+", "", rest)

    changed = False
    for hunk in _diff_hunks(event.diff):
        # ordered: a whitespace reformat keeps line order, so a reorder is a real change
        adds = [norm(line) for line in hunk if line.startswith("+")]
        dels = [norm(line) for line in hunk if line.startswith("-")]
        if not adds and not dels:
            continue
        changed = True
        if adds != dels:  # a hunk with a real change -- not cosmetic
            return CheckResult(result="pass", evidence="diff changes behavior")
    if changed:
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
    # reason comes first so the model writes its claim-by-claim check before it
    # commits to the verdict, which makes the verdict steadier across runs.
    reason: str = ""
    mismatch: bool = False  # default to "matches" if the field is absent


def diff_matches_description(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if not event.diff:
        return CheckResult(result="unknown", evidence="no diff")
    msg = [
        {
            "role": "system",
            "content": (
                "Verify a PR's description against its actual diff, claim by claim.\n"
                "1. From the description, extract the concrete, checkable changes it claims "
                "to make -- specific files, functions, behaviors, or additions. Ignore vague "
                "or high-level framing.\n"
                "2. For each concrete claim, decide whether the diff actually contains it. "
                "Write this claim-by-claim list in `reason`.\n"
                "3. Set mismatch=true when the description claims specific changes that are "
                "absent from the diff, or the diff's changes are unrelated to what the "
                "description claims. Set mismatch=false when every concrete claim is present, "
                "or the description is only high-level with no specific claim to contradict.\n"
                "Do NOT flag a PR for a merely terse or vague description, for stylistic "
                "wording, or when the diff is a reasonable subset of a broad description."
            ),
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


class _Substantive(BaseModel):
    # reason first so the model weighs the description before it commits to a verdict
    reason: str = ""
    is_low_effort: bool = False  # default to "substantive" if the field is absent


def substantive_description(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    files = len(event.changed_files or [])
    diff_lines = len((event.diff or "").splitlines())
    msg = [
        {
            "role": "system",
            "content": (
                "Judge whether a PR's description does the minimum work of explaining its "
                "change. Set is_low_effort=true ONLY when the description is effectively "
                "empty -- blank, an untouched PR template (section headers with nothing "
                "filled in), or a meaningless placeholder like 'V1' / 'update' / 'wip' -- AND "
                "it gives no explanation of what the change does or why. Weigh it against the "
                "size of the change: a large change with no explanation is low-effort; a "
                "small, self-evident change with a short but clear title is fine. Set "
                "is_low_effort=false for any description that genuinely explains the change, "
                "however terse, and never flag a PR merely for being brief. Write your "
                "reasoning in reason first."
            ),
        },
        {
            "role": "user",
            "content": (
                f"TITLE:\n{event.title}\n\nDESCRIPTION:\n{event.body}\n\n"
                f"CHANGE SIZE: {files} files, {diff_lines} diff lines"
            ),
        },
    ]
    out = llm(msg, schema=_Substantive, role="checks")
    return CheckResult(
        result="fail" if out.is_low_effort else "pass",
        evidence=out.reason,
        engine="LLM",
    )


class _FixAddresses(BaseModel):
    # reason first so the model identifies the reported cause before it concludes
    reason: str = ""
    addresses: bool = True  # default to "addresses" if the field is absent


def fix_addresses_issue(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    issues = event.linked_issues or []
    if not issues or not event.diff:
        return CheckResult(result="unknown", evidence="no linked issue to verify against")
    listing = "\n\n".join(f"ISSUE #{i.number}: {i.title}\n{i.body}" for i in issues)
    msg = [
        {
            "role": "system",
            "content": (
                "A PR claims to fix the linked issue(s). Decide whether its diff actually "
                "addresses the cause the issue describes.\n"
                "1. From the issue, identify the reported cause -- the specific component, "
                "code path, or behavior that is failing.\n"
                "2. Check whether the diff changes that same component / path / behavior.\n"
                "3. Set addresses=false ONLY when the diff plainly targets a different "
                "component or path than the one the issue reports -- a misdirected fix that "
                "would not resolve the reported problem. Set addresses=true when the diff "
                "changes the reported area, or when you cannot tell which area is at fault. "
                "Be conservative: do not flag a partial or imperfect fix, only a clearly "
                "misdirected one. Write your reasoning in reason first."
            ),
        },
        {
            "role": "user",
            "content": (
                f"PR TITLE:\n{event.title}\n\nPR DESCRIPTION:\n{event.body}\n\n"
                f"LINKED {listing}\n\nDIFF:\n{event.diff}"
            ),
        },
    ]
    out = llm(msg, schema=_FixAddresses, role="checks")
    return CheckResult(
        result="pass" if out.addresses else "fail",
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
