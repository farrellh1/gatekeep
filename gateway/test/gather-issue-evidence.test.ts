import { describe, it, expect, vi } from "vitest";
import { gatherIssueEvidence } from "../src/normalize.js";

function fakeOctokit() {
  return {
    rest: {
      issues: {
        listForRepo: vi.fn().mockResolvedValue({
          data: [
            {
              number: 42,
              title: "App crashes on launch",
              body: "stack trace",
              pull_request: undefined,
            },
            {
              number: 41,
              title: "Older open issue",
              body: "still relevant",
              pull_request: undefined,
            },
            { number: 40, title: "A pull request", body: "code", pull_request: { url: "..." } },
          ],
        }),
      },
      users: {
        getByUsername: vi.fn().mockResolvedValue({ data: { created_at: "2020-01-01T00:00:00Z" } }),
      },
    },
  };
}

const issuePayload = {
  action: "opened",
  issue: {
    number: 42,
    title: "App crashes on launch",
    body: "stack trace attached",
    user: { login: "reporter" },
    author_association: "NONE",
  },
  repository: { owner: { login: "o" }, name: "r", default_branch: "main" },
};

describe("gatherIssueEvidence", () => {
  it("collects the raw open-issue candidates without filtering or mapping", async () => {
    const ok = fakeOctokit();
    const raw = await gatherIssueEvidence(
      ok as any,
      issuePayload as any,
      "dlv-9",
      "/tmp/gatekeep/clones/o-r",
    );

    expect(raw.kind).toBe("issue");
    expect(raw.action).toBe("opened");
    expect(raw.number).toBe(42);
    expect(raw.title).toBe("App crashes on launch");
    expect(raw.body).toBe("stack trace attached");
    expect(raw.repo).toEqual({
      owner: "o",
      name: "r",
      default_branch: "main",
      clone_path: "/tmp/gatekeep/clones/o-r",
    });
    // issue-shaped evidence carries no diff/files/CI
    expect(raw.diff).toBeNull();
    expect(raw.changed_files).toBeNull();
    expect(raw.ci_state).toBeNull();
    expect(raw.author).toEqual({
      login: "reporter",
      author_association: "NONE",
      created_at: "2020-01-01T00:00:00Z",
    });

    // raw, unfiltered candidates: current issue + PR still present, PRs flagged
    expect(raw.issue_candidates).toEqual([
      { number: 42, title: "App crashes on launch", body: "stack trace", is_pull_request: false },
      { number: 41, title: "Older open issue", body: "still relevant", is_pull_request: false },
      { number: 40, title: "A pull request", body: "code", is_pull_request: true },
    ]);
  });
});
