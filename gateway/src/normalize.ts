import type { Octokit } from "octokit";
import type { NormalizedEvent, PRPayload, IssuePayload } from "./types.js";

type Clock = () => Date;
const DAY = 1000 * 60 * 60 * 24;

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
  return [...files.slice(0, MAX_CHANGED_FILES), `[gatekeep: +${files.length - MAX_CHANGED_FILES} more files truncated]`];
}

export async function normalizePullRequest(
  octokit: Octokit, payload: PRPayload, deliveryId: string, clonePath: string,
  clock: Clock = () => new Date(),
): Promise<NormalizedEvent> {
  const pr = payload.pull_request;
  const owner = pr.base.repo.owner!.login;
  const name = pr.base.repo.name;

  const diffRes = await octokit.rest.pulls.get({
    owner, repo: name, pull_number: pr.number, mediaType: { format: "diff" },
  });
  const files = await octokit.rest.pulls.listFiles({ owner, repo: name, pull_number: pr.number });
  const status = await octokit.rest.repos.getCombinedStatusForRef({
    owner, repo: name, ref: pr.head.sha,
  });
  const user = await octokit.rest.users.getByUsername({ username: pr.user!.login });

  const ciMap: Record<string, NormalizedEvent["ci_status"]> = {
    success: "success", failure: "failure", pending: "pending",
  };

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
      account_age_days: accountAgeDays(user.data.created_at, clock()),
      is_first_time_contributor:
        pr.author_association === "FIRST_TIME_CONTRIBUTOR" || pr.author_association === "NONE",
    },
    diff: capDiff(diffRes.data as unknown as string),
    changed_files: capFiles(files.data.map((f) => f.filename)),
    ci_status: ciMap[status.data.state] ?? "none",
    existing_issues: null,
  };
}

export async function normalizeIssue(
  octokit: Octokit, payload: IssuePayload, deliveryId: string, clonePath: string,
  clock: Clock = () => new Date(),
): Promise<NormalizedEvent> {
  const issue = payload.issue;
  const owner = payload.repository.owner.login;
  const name = payload.repository.name;

  const open = await octokit.rest.issues.listForRepo({
    owner, repo: name, state: "open", per_page: 30,
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
      account_age_days: accountAgeDays(user.data.created_at, clock()),
      is_first_time_contributor:
        issue.author_association === "FIRST_TIME_CONTRIBUTOR" || issue.author_association === "NONE",
    },
    diff: null,
    changed_files: null,
    ci_status: null,
    existing_issues: open.data
      .filter((i) => i.number !== issue.number && !i.pull_request)
      .map((i) => ({ number: i.number, title: i.title ?? "", body: i.body ?? "" })),
  };
}
