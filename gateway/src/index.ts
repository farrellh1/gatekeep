import "dotenv/config";
import { createServer } from "node:http";
import { App } from "octokit";
import { createNodeMiddleware } from "@octokit/webhooks";
import { handleEvent, defaultDeps, type EventCtx } from "./pipeline.js";
import type { WebhookPayload } from "./types.js";

const appId = process.env.GITHUB_APP_ID;
const rawPrivateKey = process.env.GITHUB_PRIVATE_KEY;
const webhookSecret = process.env.GITHUB_WEBHOOK_SECRET;
if (!appId || !rawPrivateKey || !webhookSecret) {
  throw new Error(
    "Missing required env: GITHUB_APP_ID, GITHUB_PRIVATE_KEY, GITHUB_WEBHOOK_SECRET",
  );
}
const privateKey = rawPrivateKey.replace(/\\n/g, "\n");
const brainUrl = process.env.BRAIN_URL ?? "http://localhost:8000";
const port = Number(process.env.PORT ?? 3000);

const app = new App({ appId, privateKey, webhooks: { secret: webhookSecret } });
const deps = defaultDeps();

const ACTIONS = new Set(["opened", "edited", "reopened"]);

function ctxFrom(kind: "issue" | "pull_request", payload: WebhookPayload, deliveryId: string): EventCtx | null {
  if (!ACTIONS.has(payload.action)) return null;
  if (!payload.installation?.id) return null;
  const r = payload.repository;
  return {
    app, installationId: payload.installation.id,
    repo: {
      owner: r.owner.login, name: r.name, default_branch: r.default_branch,
      clone_url: r.clone_url ?? `https://github.com/${r.owner.login}/${r.name}.git`,
    },
    payload, kind, deliveryId, brainUrl,
  };
}

app.webhooks.on(
  ["issues.opened", "issues.edited", "issues.reopened"],
  async ({ payload, id }) => {
    const ctx = ctxFrom("issue", payload, id);
    if (ctx) await handleEvent(ctx, deps).catch((e) => console.error("issue handler:", e));
  },
);
app.webhooks.on(
  ["pull_request.opened", "pull_request.edited", "pull_request.reopened"],
  async ({ payload, id }) => {
    const ctx = ctxFrom("pull_request", payload, id);
    if (ctx) await handleEvent(ctx, deps).catch((e) => console.error("pr handler:", e));
  },
);

createServer(createNodeMiddleware(app.webhooks)).listen(port, () => {
  console.log(`gatekeep gateway listening on :${port} (brain at ${brainUrl})`);
});
