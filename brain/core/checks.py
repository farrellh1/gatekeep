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

# An oversized diff would exceed the check model's context window. The head is
# a bounded sample of the change; the truncation marker tells the model (and a
# reviewer) that it did not see everything.
_DIFF_PROMPT_LIMIT = 80_000  # characters of diff an LLM check may embed in its prompt


def truncated_diff(diff: str) -> str:
    """Return the diff unchanged when within the limit, else truncate with a marker.

    The marker records how many characters were shown versus the original length
    so reviewers can tell whether the model saw a representative sample.
    """
    if len(diff) <= _DIFF_PROMPT_LIMIT:
        return diff
    shown = diff[:_DIFF_PROMPT_LIMIT]
    total = len(diff)
    return f"{shown}\n[... diff truncated: {_DIFF_PROMPT_LIMIT} of {total} characters shown ...]"


# conventional-commit type prefixes appear in backtick-quoted subjects like
# `fix(api): ...` and are not code citations
CONVENTIONAL_COMMIT_PREFIXES: frozenset[str] = frozenset(
    ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test"]
)


def cited_symbols(text: str) -> set[str]:
    cited: set[str] = set()
    for span in _CODE_SPAN_RE.findall(text):
        for ref in _CALL_REF_RE.findall(span):
            name = ref.split(".")[-1]
            if name not in CONVENTIONAL_COMMIT_PREFIXES:
                cited.add(name)
    return cited


def diff_identifiers(diff: str | None) -> set[str]:
    """Identifiers from all diff lines: added (+), removed (-), and context.

    A symbol that appears anywhere in the diff is anchored in the actual change:
    added symbols aren't in the base-branch index yet, removed symbols are the
    subject of the change, and context lines show the surrounding real code.
    None of these are hallucinated citations; the check targets prose citations
    with no anchor in either the repo index or the diff.
    """
    if not diff:
        return set()
    lines = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "diff --git", "@@ ")):
            continue
        if line.startswith(("+", "-")):
            lines.append(line[1:])
        else:
            lines.append(line)
    return set(_IDENT_RE.findall("\n".join(lines)))


def cited_symbols_exist(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    reader = ctx.reader
    cited = cited_symbols(f"{event.title}\n{event.body}")
    if not cited:
        return CheckResult(result="pass", evidence="no specific code symbols cited")
    diff_idents = diff_identifiers(event.diff)
    missing = [name for name in cited if not reader.symbol_exists(name) and name not in diff_idents]
    # grade confidence against the languages the change touches
    relevant = {Path(f).suffix for f in (event.changed_files or [])}
    confidence, engine = reader.evidence_grade(relevant or None)
    if missing:
        names = ", ".join(f"`{m}()`" for m in sorted(missing))
        evidence = f"references {names} which do not exist in this repo"
        # one unresolved name is weak evidence — external libraries, syscalls, and
        # dependency symbols are absent from the repo index and would produce a
        # single-name miss
        if len(set(missing)) == 1:
            confidence = "LOW"
        return CheckResult(
            result="fail",
            evidence=evidence,
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
            "content": (
                f"DESCRIPTION:\n{event.title}\n{event.body}\n\nDIFF:\n{truncated_diff(event.diff)}"
            ),
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
                f"LINKED {listing}\n\nDIFF:\n{truncated_diff(event.diff)}"
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


def first_contribution(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    """An author with no track record in this repo is a prior, not evidence of
    wrongdoing. The finding is graded LOW so it can only nudge a verdict toward
    asking for verification evidence -- the Judge is instructed it must never
    contribute to a 'slop' label.
    """
    author = event.author
    if not author.is_first_time_contributor:
        return CheckResult(
            result="pass",
            evidence="author has prior contributions to this repo",
        )
    if event.ci_status == "success":
        return CheckResult(
            result="pass",
            evidence="first-time contributor, but CI success corroborates the change",
        )
    return CheckResult(
        result="fail",
        confidence="LOW",
        evidence=(
            f"author's first contribution to this repo (account "
            f"{author.account_age_days} days old) with no CI corroboration -- "
            "no track record to support the PR's claims"
        ),
    )


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A link token is a markdown image, a markdown link, or a raw URL. Matched
# token-by-token (never with a repetition over the whole line) so an adversarial
# body cannot trigger exponential backtracking.
_LINK_TOKEN_RE = re.compile(r"^(?:!\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\([^)]*\)|https?://\S+)$")


def _is_bare_link_line(line: str) -> bool:
    """True when every whitespace-separated token on the line is a link token --
    the line adds no prose content."""
    tokens = line.split()
    if not tokens:
        return False
    return all(_LINK_TOKEN_RE.match(token) for token in tokens)


def template_untouched(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    body = event.body or ""

    # Strip HTML comments (typically template instructions).
    stripped = _HTML_COMMENT_RE.sub("", body)

    novel_lines = []
    for line in stripped.splitlines():
        bare = line.strip()
        if not bare:
            continue
        # markdown headings need a space after the hashes; `#123` issue
        # references are prose, not headings
        if re.match(r"^#{1,6}\s", bare) or bare in ("#", "##", "###"):
            continue
        # an unchecked template checkbox is untouched boilerplate; a checked
        # box shows the author interacted with the template, so it counts
        if re.match(r"^[-*] \[ \]", bare):
            continue
        if re.match(r"^(---|\*\*\*)$", bare):
            continue
        if _is_bare_link_line(line):
            continue
        novel_lines.append(line)

    novel_chars = len(re.sub(r"\s+", "", "".join(novel_lines)))
    diff_lines = len((event.diff or "").splitlines())

    if novel_chars >= 20:
        return CheckResult(
            result="pass",
            evidence="description has substantive content beyond template boilerplate",
        )

    confidence = "HIGH" if diff_lines >= 50 else "LOW"
    return CheckResult(
        result="fail",
        evidence=f"description is empty or an untouched template for a {diff_lines}-line change",
        confidence=confidence,
    )


_TEST_FILE_RE = re.compile(
    r"(?:^|/)(?:test_[^/]+|[^/]+_test\.[^/]+|[^/]+\.test\.[^/]+|[^/]+\.spec\.[^/]+)$"
    r"|(?:^|/)(?:test|tests|__tests__|spec)/",
    re.IGNORECASE,
)

# the word gap after "adds/added" is bounded so the pattern cannot reach across
# clause boundaries and read an unrelated mention of tests as a claim
_ADDED_TESTS_RE = re.compile(
    r"(?:adds?|added)\s+(?:\w+\s+){0,3}tests?\b"
    r"|new\s+tests?"
    r"|test\s+coverage\s+added"
    r"|(?:unit|integration)\s+tests?\s+(?:are\s+)?(?:included|added)",
    re.IGNORECASE,
)

_RAN_VERIFICATION_RE = re.compile(
    r"(?:all\s+|existing\s+)?tests?\s+pass(?:ing|ed)?"
    r"|\d+/\d+\s+tests?"
    r"|tests?\s+\d+/\d+"
    r"|lint\s+(?:is\s+)?clean"
    r"|verified\s+locally"
    r"|manually\s+tested"
    r"|tested\s+locally"
    r"|CI\s+(?:is\s+)?(?:green|passing)",
    re.IGNORECASE,
)


def _touches_tests(changed_files: list[str] | None) -> bool:
    if not changed_files:
        return False
    return any(_TEST_FILE_RE.search(f) for f in changed_files)


# words that turn a verification phrase into an instruction, a hope, or a
# negation rather than the author's claim of work performed
_NON_CLAIM_CONTEXT_RE = re.compile(
    r"(?:please|ensure|make\s+sure|should|must|once|before|until|haven't|hasn't|"
    r"not|n't|todo|to\s+do)\s*\W*$",
    re.IGNORECASE,
)


def _claim_matches(pattern: re.Pattern, text: str) -> list[str]:
    """Pattern matches that read as the author claiming completed verification.

    A match whose preceding few words are instructional or negating ("please make
    sure all tests pass", "I have not verified locally") is not a claim and is
    dropped.
    """
    claims = []
    for m in pattern.finditer(text):
        preceding = text[max(0, m.start() - 40) : m.start()]
        if _NON_CLAIM_CONTEXT_RE.search(preceding):
            continue
        claims.append(m.group(0))
    return claims


def verification_claims_unsupported(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    # HTML comments hold template instructions ("confirm all tests pass"), which
    # are not the author's claims
    text = _HTML_COMMENT_RE.sub("", f"{event.title or ''}\n{event.body or ''}")

    added_tests_claims = _claim_matches(_ADDED_TESTS_RE, text)
    ran_verification_claims = _claim_matches(_RAN_VERIFICATION_RE, text)

    if not added_tests_claims and not ran_verification_claims:
        return CheckResult(result="pass", evidence="no verification claims to corroborate")

    if event.ci_status == "success":
        return CheckResult(result="pass", evidence="CI success corroborates verification claims")

    if event.ci_status == "failure":
        claims_text = "; ".join((added_tests_claims + ran_verification_claims)[:3])
        return CheckResult(
            result="fail",
            confidence="HIGH",
            evidence=f'description claims verification ("{claims_text}") but CI is failing',
        )

    if added_tests_claims and not _touches_tests(event.changed_files):
        return CheckResult(
            result="fail",
            confidence="HIGH",
            evidence=(
                f'description claims tests were added ("{added_tests_claims[0]}") '
                "but the diff touches no test file"
            ),
        )

    if ran_verification_claims and not _touches_tests(event.changed_files):
        claims_text = "; ".join(ran_verification_claims[:3])
        return CheckResult(
            result="fail",
            confidence="LOW",
            evidence=(
                f'description claims verification ("{claims_text}") but no CI '
                "result or test-file changes corroborate it"
            ),
        )

    return CheckResult(result="pass", evidence="verification claims are corroborated")


class _ClaimedBug(BaseModel):
    # reason first so the model examines the pre-change code before committing to a verdict
    reason: str = ""
    is_bug_fix_claim: bool = True
    contradicted: bool = False  # default to "bug plausible" if absent


def claimed_bug_exists(event: NormalizedEvent, ctx: RunContext) -> CheckResult:
    if not event.diff:
        return CheckResult(result="unknown", evidence="no diff")

    msg = [
        {
            "role": "system",
            "content": (
                "You are reviewing a PR to decide whether its bug-fix claim is contradicted "
                "by its own diff.\n"
                "1. Decide whether the description claims to FIX a defect in existing behavior "
                "(as opposed to adding a feature, docs, refactor, or dependency bump). "
                "If not, set is_bug_fix_claim=false.\n"
                "2. If it does: the diff's removed (-) and context lines show the pre-change code. "
                "Check whether the specific defective behavior the description claims -- "
                "missing check, wrong ordering, ignored value, etc. -- is actually visible "
                "in that pre-change code.\n"
                "3. Set contradicted=true ONLY when the pre-change code plainly contradicts the "
                "claim -- e.g. the guard the description says is missing is already present, "
                "the described faulty path does not exist in the removed/context lines, or the "
                "diff is purely additive while the description claims existing logic was wrong. "
                "Set contradicted=false when the claim is consistent with the visible code or "
                "the diff shows too little context to tell. Be conservative; do not flag a "
                "plausible fix.\n"
                "Write the reasoning in `reason`."
            ),
        },
        {
            "role": "user",
            "content": (
                f"TITLE:\n{event.title}\n\nDESCRIPTION:\n{event.body}\n\n"
                f"DIFF:\n{truncated_diff(event.diff)}"
            ),
        },
    ]
    out = llm(msg, schema=_ClaimedBug, role="checks")

    if not out.is_bug_fix_claim:
        return CheckResult(
            result="unknown", evidence="description does not claim a bug fix", engine="LLM"
        )
    if out.contradicted:
        return CheckResult(result="fail", evidence=out.reason, engine="LLM")
    return CheckResult(result="pass", evidence=out.reason, engine="LLM")


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
