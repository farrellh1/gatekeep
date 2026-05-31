import { describe, it, expect, vi } from "vitest";
import { normalizePullRequest } from "../src/normalize.js";

function fakeOctokit() {
  return {
    rest: {
      pulls: {
        get: vi.fn().mockResolvedValue({ data: "--- a/x\n+++ b/x\n+# noop\n" }),
        listFiles: vi.fn().mockResolvedValue({ data: [{ filename: "src/auth.py" }] }),
      },
      repos: {
        getCombinedStatusForRef: vi.fn().mockResolvedValue({ data: { state: "failure" } }),
      },
      users: {
        getByUsername: vi.fn().mockResolvedValue({ data: { created_at: "2020-01-01T00:00:00Z" } }),
      },
    },
  };
}

const prPayload = {
  action: "opened",
  number: 7,
  pull_request: {
    number: 7,
    title: "Fix auth",
    body: "calls validateToken()",
    user: { login: "octocat" },
    author_association: "FIRST_TIME_CONTRIBUTOR",
    head: { sha: "abc123" },
    base: { repo: { owner: { login: "o" }, name: "r", default_branch: "main" } },
  },
};

describe("normalizePullRequest", () => {
  it("assembles a NormalizedEvent from payload + octokit reads", async () => {
    const ok = fakeOctokit();
    const ev = await normalizePullRequest(
      ok as any,
      prPayload as any,
      "dlv-1",
      "/tmp/gatekeep/clones/o-r",
      () => new Date("2021-01-01T00:00:00Z"),
    );
    expect(ev.kind).toBe("pull_request");
    expect(ev.number).toBe(7);
    expect(ev.diff).toContain("# noop");
    expect(ev.changed_files).toEqual(["src/auth.py"]);
    expect(ev.ci_status).toBe("failure");
    expect(ev.repo.clone_path).toBe("/tmp/gatekeep/clones/o-r");
    expect(ev.author.is_first_time_contributor).toBe(true);
    expect(ev.author.account_age_days).toBe(366);
  });

  it("caps an oversized diff and an oversized file list", async () => {
    process.env.GATEKEEP_MAX_DIFF_BYTES = "100";
    process.env.GATEKEEP_MAX_CHANGED_FILES = "2";
    vi.resetModules();
    const { normalizePullRequest: fresh } = await import("../src/normalize.js");

    const ok = fakeOctokit();
    ok.rest.pulls.get = vi.fn().mockResolvedValue({ data: "x".repeat(5000) });
    ok.rest.pulls.listFiles = vi.fn().mockResolvedValue({
      data: [{ filename: "a" }, { filename: "b" }, { filename: "c" }, { filename: "d" }],
    });

    const ev = await fresh(
      ok as any,
      prPayload as any,
      "dlv-2",
      "/tmp/gatekeep/clones/o-r",
      () => new Date("2021-01-01T00:00:00Z"),
    );
    expect(ev.diff).toContain("[gatekeep: diff truncated to 100 of 5000 bytes]");
    expect(ev.diff!.length).toBeLessThan(5000);
    expect(ev.changed_files).toEqual(["a", "b", "[gatekeep: +2 more files truncated]"]);

    delete process.env.GATEKEEP_MAX_DIFF_BYTES;
    delete process.env.GATEKEEP_MAX_CHANGED_FILES;
  });
});
