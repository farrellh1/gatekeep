from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RepoRef(BaseModel):
    owner: str
    name: str
    default_branch: str
    clone_path: str  # local path to the checked-out clone (filesystem only)


class AuthorInfo(BaseModel):
    login: str
    account_age_days: int
    is_first_time_contributor: bool


class IssueRef(BaseModel):
    number: int
    title: str
    body: str


class NormalizedEvent(BaseModel):
    """Gateway -> Brain contract. Gateway produces this; Brain only consumes.

    Note the absence of any GitHub client or token: the Brain is structurally
    incapable of reaching GitHub. Repo content is read from `repo.clone_path`.
    """

    delivery_id: str
    kind: Literal["issue", "pull_request"]
    action: str
    repo: RepoRef
    number: int
    title: str
    body: str
    author: AuthorInfo
    # PR-only
    diff: str | None = None
    changed_files: list[str] | None = None
    ci_status: Literal["success", "failure", "pending", "none"] | None = None
    # issue-only (Gateway supplies open-issue candidates for dupe detection)
    existing_issues: list[IssueRef] | None = None


class Finding(BaseModel):
    check: str
    result: Literal["pass", "fail", "unknown"]
    evidence: str  # human-readable; becomes the comment text
    confidence: Literal["HIGH", "LOW"] = "HIGH"  # LOW evidence must not drive a harsh verdict alone
    engine: Literal["AST_TREE_SITTER", "HEURISTIC", "DETERMINISTIC", "LLM"] = "DETERMINISTIC"


class IntakeResult(BaseModel):
    kind: Literal["issue", "pull_request"]
    relevant: bool
    route: Literal["firewall", "skip"]
    reason: str


class Verdict(BaseModel):
    label: Literal["slop", "needs-info", "legit"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = []
    primary_evidence: str = ""


class Action(BaseModel):
    action: Literal["comment", "label", "close"]
    body: str | None = None


class GateInfo(BaseModel):
    policy: str
    gated: list[str] = []  # names of actions stripped by the gate
    reason: str = ""


class BrainState(BaseModel):
    event: NormalizedEvent
    intake: IntakeResult | None = None
    findings: list[Finding] = []
    verdict: Verdict | None = None
    actions: list[Action] = []
    gate: GateInfo | None = None
