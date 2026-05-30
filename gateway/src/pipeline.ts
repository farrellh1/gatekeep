import type { App, Octokit } from "octokit";
import { installationOctokit as realAuth } from "./auth.js";
import { ensureClone as realClone, type CloneRepo } from "./clone.js";
import { fetchConfig as realConfig } from "./config.js";
import { normalizePullRequest, normalizeIssue } from "./normalize.js";
import { callBrain as realBrain } from "./brainClient.js";
import { execute as realExecute } from "./execute.js";
import type { WebhookPayload, NormalizedEvent } from "./types.js";

export interface EventCtx {
  app: App;
  installationId: number;
  repo: CloneRepo;
  payload: WebhookPayload;
  kind: "issue" | "pull_request";
  deliveryId: string;
  brainUrl: string;
}

export interface Deps {
  installationOctokit: typeof realAuth;
  ensureClone: typeof realClone;
  fetchConfig: typeof realConfig;
  normalize: (octokit: Octokit, payload: WebhookPayload, deliveryId: string, clonePath: string) => Promise<NormalizedEvent>;
  callBrain: typeof realBrain;
  execute: typeof realExecute;
}

export async function handleEvent(ctx: EventCtx, deps: Deps): Promise<void> {
  const octokit = await deps.installationOctokit(ctx.app, ctx.installationId);
  const clonePath = await deps.ensureClone(ctx.repo);
  const configYaml = await deps.fetchConfig(octokit, { owner: ctx.repo.owner, repo: ctx.repo.name });
  const event = await deps.normalize(octokit, ctx.payload, ctx.deliveryId, clonePath);
  const result = await deps.callBrain(event, configYaml, ctx.brainUrl);

  if (result.intake?.route === "skip") return;
  await deps.execute(octokit,
    { owner: ctx.repo.owner, repo: ctx.repo.name, issue_number: event.number },
    result.actions);
}

export function defaultDeps(): Deps {
  return {
    installationOctokit: realAuth,
    ensureClone: realClone,
    fetchConfig: realConfig,
    normalize: (octokit, payload, deliveryId, clonePath) =>
      "pull_request" in payload
        ? normalizePullRequest(octokit, payload, deliveryId, clonePath)
        : normalizeIssue(octokit, payload, deliveryId, clonePath),
    callBrain: realBrain,
    execute: realExecute,
  };
}
