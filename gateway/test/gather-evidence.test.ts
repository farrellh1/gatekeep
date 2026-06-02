import { describe, it, expect, vi } from "vitest";
import { gatherEvidence } from "../src/normalize.js";

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

describe("gatherEvidence (PR)", () => {
  it("collects the raw GitHub reads without applying any mapping", async () => {
    const ok = fakeOctokit();
    const raw = await gatherEvidence(
      ok as any,
      prPayload as any,
      "dlv-1",
      "/tmp/gatekeep/clones/o-r",
    );

    // raw record carries unmapped evidence + the payload facts the mapper needs
    expect(raw.kind).toBe("pull_request");
    expect(raw.action).toBe("opened");
    expect(raw.number).toBe(7);
    expect(raw.title).toBe("Fix auth");
    expect(raw.body).toBe("calls validateToken()");
    expect(raw.repo).toEqual({
      owner: "o",
      name: "r",
      default_branch: "main",
      clone_path: "/tmp/gatekeep/clones/o-r",
    });
    expect(raw.diff).toBe("--- a/x\n+++ b/x\n+# noop\n");
    expect(raw.changed_files).toEqual(["src/auth.py"]);
    // unmapped state string, not a NormalizedEvent ci_status
    expect(raw.ci_state).toBe("failure");
    expect(raw.author).toEqual({
      login: "octocat",
      author_association: "FIRST_TIME_CONTRIBUTOR",
      created_at: "2020-01-01T00:00:00Z",
    });
    expect(raw.issue_candidates).toBeNull();

    // adapter reads the head ref for combined status
    expect(ok.rest.repos.getCombinedStatusForRef).toHaveBeenCalledWith(
      expect.objectContaining({ ref: "abc123" }),
    );
  });

  it("does not cap the diff or file list (mapping lives in the pure mapper)", async () => {
    process.env.GATEKEEP_MAX_DIFF_BYTES = "10";
    process.env.GATEKEEP_MAX_CHANGED_FILES = "1";
    vi.resetModules();
    const { gatherEvidence: fresh } = await import("../src/normalize.js");

    const ok = fakeOctokit();
    ok.rest.pulls.get = vi.fn().mockResolvedValue({ data: "y".repeat(500) });
    ok.rest.pulls.listFiles = vi.fn().mockResolvedValue({
      data: [{ filename: "a" }, { filename: "b" }, { filename: "c" }],
    });

    const raw = await fresh(ok as any, prPayload as any, "dlv-2", "/tmp/gatekeep/clones/o-r");
    expect(raw.diff).toBe("y".repeat(500));
    expect(raw.changed_files).toEqual(["a", "b", "c"]);

    delete process.env.GATEKEEP_MAX_DIFF_BYTES;
    delete process.env.GATEKEEP_MAX_CHANGED_FILES;
  });
});
