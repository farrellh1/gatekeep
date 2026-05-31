import { describe, it, expect, vi } from "vitest";
import { handleEvent } from "../src/pipeline.js";
import type { WebhookPayload } from "../src/types.js";

function deps() {
  return {
    installationOctokit: vi.fn().mockResolvedValue({ id: "octo" }),
    ensureClone: vi.fn().mockResolvedValue("/tmp/clone"),
    fetchConfig: vi.fn().mockResolvedValue("mode: suggest-only"),
    normalize: vi.fn().mockResolvedValue({ number: 7, repo: { owner: "o", name: "r" } }),
    callBrain: vi.fn().mockResolvedValue({
      intake: { route: "firewall" },
      actions: [{ action: "comment", body: "note" }],
    }),
    execute: vi.fn().mockResolvedValue(undefined),
  };
}
const ctx = {
  app: {} as any,
  installationId: 42,
  repo: { owner: "o", name: "r", default_branch: "main", clone_url: "https://github.com/o/r.git" },
  payload: {} as WebhookPayload,
  kind: "pull_request" as const,
  deliveryId: "d1",
  brainUrl: "http://brain",
};

describe("handleEvent", () => {
  it("runs auth→clone→config→normalize→brain→execute and passes actions through", async () => {
    const d = deps();
    await handleEvent(ctx, d as any);
    expect(d.ensureClone).toHaveBeenCalled();
    expect(d.callBrain).toHaveBeenCalledWith(
      { number: 7, repo: { owner: "o", name: "r" } },
      "mode: suggest-only",
      "http://brain",
    );
    expect(d.execute).toHaveBeenCalledWith(
      { id: "octo" },
      { owner: "o", repo: "r", issue_number: 7 },
      [{ action: "comment", body: "note" }],
    );
  });

  it("skips execute when the brain routes to skip", async () => {
    const d = deps();
    d.callBrain = vi.fn().mockResolvedValue({ intake: { route: "skip" }, actions: [] });
    await handleEvent(ctx, d as any);
    expect(d.execute).not.toHaveBeenCalled();
  });
});
