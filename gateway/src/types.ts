import type { EmitterWebhookEvent } from "@octokit/webhooks";

export type IssuePayload =
  | EmitterWebhookEvent<"issues.opened">["payload"]
  | EmitterWebhookEvent<"issues.edited">["payload"]
  | EmitterWebhookEvent<"issues.reopened">["payload"];

export type PRPayload =
  | EmitterWebhookEvent<"pull_request.opened">["payload"]
  | EmitterWebhookEvent<"pull_request.edited">["payload"]
  | EmitterWebhookEvent<"pull_request.reopened">["payload"];

export type WebhookPayload = IssuePayload | PRPayload;

export interface RepoRef {
  owner: string;
  name: string;
  default_branch: string;
  clone_path: string;
}

export interface AuthorInfo {
  login: string;
  account_age_days: number;
  is_first_time_contributor: boolean;
}

export interface IssueRef {
  number: number;
  title: string;
  body: string;
}

export interface NormalizedEvent {
  delivery_id: string;
  kind: "issue" | "pull_request";
  action: string;
  repo: RepoRef;
  number: number;
  title: string;
  body: string;
  author: AuthorInfo;
  diff?: string | null;
  changed_files?: string[] | null;
  ci_status?: "success" | "failure" | "pending" | "none" | null;
  existing_issues?: IssueRef[] | null;
}

export interface Action {
  action: "comment" | "label" | "close";
  body?: string | null;
}

export interface BrainResult {
  intake?: { kind: string; relevant: boolean; route: "firewall" | "skip"; reason: string } | null;
  findings: {
    check: string;
    result: "pass" | "fail" | "unknown";
    evidence: string;
    confidence?: "HIGH" | "LOW";
    engine?: "AST_TREE_SITTER" | "HEURISTIC" | "DETERMINISTIC" | "LLM";
  }[];
  verdict?: { label: "slop" | "needs-info" | "legit"; confidence: number; reasons: string[]; primary_evidence: string } | null;
  actions: Action[];
  gate?: { policy: string; gated: string[]; reason: string } | null;
  trace?: {
    delivery_id: string;
    kind: string;
    number: number;
    steps: { node: string; elapsed_ms: number; summary: string; output: Record<string, unknown> }[];
  } | null;
}
