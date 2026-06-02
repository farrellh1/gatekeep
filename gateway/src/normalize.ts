import type { Octokit } from "octokit";
import type {
  AuthorInfo,
  IssueRef,
  NormalizedEvent,
  PRPayload,
  IssuePayload,
  RepoRef,
} from "./types.js";

type Clock = () => Date;
const DAY = 1000 * 60 * 60 * 24;

// an open issue as returned by the list endpoint, before the mapper drops the current
// issue and any pull requests; `is_pull_request` flags the entries GitHub folds into the
// issues list but that are actually PRs
export interface IssueCandidate {
  number: number;
  title: string;
  body: string;
  is_pull_request: boolean;
}

// the GitHub reads plus the payload facts the mapper needs, with no mapping applied:
// raw diff/file list, the unmapped combined-status state, the raw open-issue candidates,
// and the author's user record
export interface RawEvidence {
  delivery_id: string;
  kind: "pull_request" | "issue";
  action: string;
  repo: RepoRef;
  number: number;
  title: string;
  body: string;
  author: { login: string; author_association: string; created_at: string };
  diff: string | null;
  changed_files: string[] | null;
  ci_state: string | null;
  issue_candidates: IssueCandidate[] | null;
}

const MAX_DIFF_BYTES = Number(process.env.GATEKEEP_MAX_DIFF_BYTES) || 1_000_000;
const MAX_CHANGED_FILES = Number(process.env.GATEKEEP_MAX_CHANGED_FILES) || 300;

function accountAgeDays(createdAt: string, now: Date): number {
  return Math.floor((now.getTime() - new Date(createdAt).getTime()) / DAY);
}

function capDiff(diff: string): string {
  const total = Buffer.byteLength(diff, "utf8");
  if (total <= MAX_DIFF_BYTES) return diff;
  const head = Buffer.from(diff, "utf8").subarray(0, MAX_DIFF_BYTES).toString("utf8");
  return `${head}\n\n[gatekeep: diff truncated to ${MAX_DIFF_BYTES} of ${total} bytes]`;
}

function capFiles(files: string[]): string[] {
  if (files.length <= MAX_CHANGED_FILES) return files;
  return [
    ...files.slice(0, MAX_CHANGED_FILES),
    `[gatekeep: +${files.length - MAX_CHANGED_FILES} more files truncated]`,
  ];
}

const ciMap: Record<string, NormalizedEvent["ci_status"]> = {
  success: "success",
  failure: "failure",
  pending: "pending",
};

// the duplicate-detection candidates minus the current issue itself and any pull requests
// (GitHub folds PRs into the issues list); returns null when there are no candidates (the PR path)
function buildExistingIssues(
  candidates: IssueCandidate[] | null,
  currentNumber: number,
): IssueRef[] | null {
  if (candidates === null) return null;
  return candidates
    .filter((c) => c.number !== currentNumber && !c.is_pull_request)
    .map((c) => ({ number: c.number, title: c.title, body: c.body }));
}

export function buildAuthor(author: RawEvidence["author"], now: Date): AuthorInfo {
  return {
    login: author.login,
    account_age_days: accountAgeDays(author.created_at, now),
    is_first_time_contributor:
      author.author_association === "FIRST_TIME_CONTRIBUTOR" ||
      author.author_association === "NONE",
  };
}

export function toNormalizedEvent(
  raw: RawEvidence,
  clock: Clock = () => new Date(),
): NormalizedEvent {
  return {
    delivery_id: raw.delivery_id,
    kind: raw.kind,
    action: raw.action,
    repo: raw.repo,
    number: raw.number,
    title: raw.title,
    body: raw.body,
    author: buildAuthor(raw.author, clock()),
    diff: raw.diff === null ? null : capDiff(raw.diff),
    changed_files: raw.changed_files === null ? null : capFiles(raw.changed_files),
    ci_status: raw.ci_state === null ? null : (ciMap[raw.ci_state] ?? "none"),
    existing_issues: buildExistingIssues(raw.issue_candidates, raw.number),
  };
}

export async function gatherEvidence(
  octokit: Octokit,
  payload: PRPayload,
  deliveryId: string,
  clonePath: string,
): Promise<RawEvidence> {
  const pr = payload.pull_request;
  const owner = pr.base.repo.owner!.login;
  const name = pr.base.repo.name;

  const diffRes = await octokit.rest.pulls.get({
    owner,
    repo: name,
    pull_number: pr.number,
    mediaType: { format: "diff" },
  });
  const files = await octokit.rest.pulls.listFiles({ owner, repo: name, pull_number: pr.number });
  const status = await octokit.rest.repos.getCombinedStatusForRef({
    owner,
    repo: name,
    ref: pr.head.sha,
  });
  const user = await octokit.rest.users.getByUsername({ username: pr.user!.login });

  return {
    delivery_id: deliveryId,
    kind: "pull_request",
    action: payload.action,
    repo: { owner, name, default_branch: pr.base.repo.default_branch, clone_path: clonePath },
    number: pr.number,
    title: pr.title ?? "",
    body: pr.body ?? "",
    author: {
      login: pr.user!.login,
      author_association: pr.author_association,
      created_at: user.data.created_at,
    },
    diff: diffRes.data as unknown as string,
    changed_files: files.data.map((f) => f.filename),
    ci_state: status.data.state,
    issue_candidates: null,
  };
}

export async function normalizePullRequest(
  octokit: Octokit,
  payload: PRPayload,
  deliveryId: string,
  clonePath: string,
  clock: Clock = () => new Date(),
): Promise<NormalizedEvent> {
  return toNormalizedEvent(await gatherEvidence(octokit, payload, deliveryId, clonePath), clock);
}

export async function gatherIssueEvidence(
  octokit: Octokit,
  payload: IssuePayload,
  deliveryId: string,
  clonePath: string,
): Promise<RawEvidence> {
  const issue = payload.issue;
  const owner = payload.repository.owner.login;
  const name = payload.repository.name;

  const open = await octokit.rest.issues.listForRepo({
    owner,
    repo: name,
    state: "open",
    per_page: 30,
  });
  const user = await octokit.rest.users.getByUsername({ username: issue.user!.login });

  return {
    delivery_id: deliveryId,
    kind: "issue",
    action: payload.action,
    repo: { owner, name, default_branch: payload.repository.default_branch, clone_path: clonePath },
    number: issue.number,
    title: issue.title ?? "",
    body: issue.body ?? "",
    author: {
      login: issue.user!.login,
      author_association: issue.author_association,
      created_at: user.data.created_at,
    },
    diff: null,
    changed_files: null,
    ci_state: null,
    issue_candidates: open.data.map((i) => ({
      number: i.number,
      title: i.title ?? "",
      body: i.body ?? "",
      is_pull_request: Boolean(i.pull_request),
    })),
  };
}

export async function normalizeIssue(
  octokit: Octokit,
  payload: IssuePayload,
  deliveryId: string,
  clonePath: string,
  clock: Clock = () => new Date(),
): Promise<NormalizedEvent> {
  return toNormalizedEvent(
    await gatherIssueEvidence(octokit, payload, deliveryId, clonePath),
    clock,
  );
}
