import { describe, it, expect, vi } from "vitest";
import { toNormalizedEvent, type RawEvidence } from "../src/normalize.js";

function prEvidence(overrides: Partial<RawEvidence> = {}): RawEvidence {
  return {
    delivery_id: "dlv-1",
    kind: "pull_request",
    action: "opened",
    repo: { owner: "o", name: "r", default_branch: "main", clone_path: "/tmp/gatekeep/clones/o-r" },
    number: 7,
    title: "Fix auth",
    body: "calls validateToken()",
    author: {
      login: "octocat",
      author_association: "FIRST_TIME_CONTRIBUTOR",
      created_at: "2020-01-01T00:00:00Z",
    },
    diff: "--- a/x\n+++ b/x\n+# noop\n",
    changed_files: ["src/auth.py"],
    ci_state: "failure",
    issue_candidates: null,
    ...overrides,
  };
}

function issueEvidence(overrides: Partial<RawEvidence> = {}): RawEvidence {
  return {
    delivery_id: "dlv-9",
    kind: "issue",
    action: "opened",
    repo: { owner: "o", name: "r", default_branch: "main", clone_path: "/tmp/gatekeep/clones/o-r" },
    number: 42,
    title: "App crashes on launch",
    body: "stack trace attached",
    author: {
      login: "reporter",
      author_association: "NONE",
      created_at: "2020-01-01T00:00:00Z",
    },
    diff: null,
    changed_files: null,
    ci_state: null,
    issue_candidates: [
      {
        number: 42,
        title: "App crashes on launch",
        body: "stack trace attached",
        is_pull_request: false,
      },
      { number: 41, title: "Older open issue", body: "still relevant", is_pull_request: false },
      { number: 40, title: "A pull request", body: "code", is_pull_request: true },
    ],
    ...overrides,
  };
}

describe("toNormalizedEvent (PR)", () => {
  it("maps raw evidence into a NormalizedEvent with no I/O", () => {
    const ev = toNormalizedEvent(prEvidence(), () => new Date("2021-01-01T00:00:00Z"));
    expect(ev.kind).toBe("pull_request");
    expect(ev.number).toBe(7);
    expect(ev.title).toBe("Fix auth");
    expect(ev.diff).toContain("# noop");
    expect(ev.changed_files).toEqual(["src/auth.py"]);
    expect(ev.ci_status).toBe("failure");
    expect(ev.repo.clone_path).toBe("/tmp/gatekeep/clones/o-r");
    expect(ev.author.login).toBe("octocat");
    expect(ev.author.is_first_time_contributor).toBe(true);
    expect(ev.author.account_age_days).toBe(366);
    expect(ev.existing_issues).toBeNull();
  });

  it("caps an oversized diff and an oversized file list", async () => {
    process.env.GATEKEEP_MAX_DIFF_BYTES = "100";
    process.env.GATEKEEP_MAX_CHANGED_FILES = "2";
    vi.resetModules();
    const { toNormalizedEvent: fresh } = await import("../src/normalize.js");

    const ev = fresh(
      prEvidence({
        diff: "x".repeat(5000),
        changed_files: ["a", "b", "c", "d"],
      }),
      () => new Date("2021-01-01T00:00:00Z"),
    );
    expect(ev.diff).toContain("[gatekeep: diff truncated to 100 of 5000 bytes]");
    expect(ev.diff!.length).toBeLessThan(5000);
    expect(ev.changed_files).toEqual(["a", "b", "[gatekeep: +2 more files truncated]"]);

    delete process.env.GATEKEEP_MAX_DIFF_BYTES;
    delete process.env.GATEKEEP_MAX_CHANGED_FILES;
  });

  it('maps an unrecognized combined-status state to "none"', () => {
    const ev = toNormalizedEvent(prEvidence({ ci_state: "error" }), () => new Date());
    expect(ev.ci_status).toBe("none");
  });

  it("leaves ci_status null when there is no combined status (issue-shaped evidence)", () => {
    const ev = toNormalizedEvent(prEvidence({ ci_state: null }), () => new Date());
    expect(ev.ci_status).toBeNull();
  });
});

describe("toNormalizedEvent (issue)", () => {
  it("maps raw issue evidence with no I/O and no diff/CI", () => {
    const ev = toNormalizedEvent(issueEvidence(), () => new Date("2021-01-01T00:00:00Z"));
    expect(ev.kind).toBe("issue");
    expect(ev.number).toBe(42);
    expect(ev.title).toBe("App crashes on launch");
    expect(ev.diff).toBeNull();
    expect(ev.changed_files).toBeNull();
    expect(ev.ci_status).toBeNull();
    expect(ev.author.login).toBe("reporter");
    expect(ev.author.is_first_time_contributor).toBe(true);
    expect(ev.author.account_age_days).toBe(366);
  });

  it("drops the current issue and any pull requests from existing_issues", () => {
    const ev = toNormalizedEvent(issueEvidence(), () => new Date("2021-01-01T00:00:00Z"));
    expect(ev.existing_issues).toEqual([
      { number: 41, title: "Older open issue", body: "still relevant" },
    ]);
  });
});
