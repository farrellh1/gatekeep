import { describe, it, expect, vi } from "vitest";
import { installationOctokit } from "../src/auth.js";

describe("installationOctokit", () => {
  it("delegates to app.getInstallationOctokit with the installation id", async () => {
    const fakeClient = { rest: {} };
    const app = { getInstallationOctokit: vi.fn().mockResolvedValue(fakeClient) };
    const client = await installationOctokit(app as any, 42);
    expect(app.getInstallationOctokit).toHaveBeenCalledWith(42);
    expect(client).toBe(fakeClient);
  });
});
