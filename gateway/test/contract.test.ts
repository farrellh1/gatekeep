import { describe, it, expect, vi } from "vitest";
import { normalizePullRequest } from "../src/normalize.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import type { NormalizedEvent } from "../src/types.js";

const SAMPLE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../brain/tests/contract/normalized_event.sample.json",
);

describe("contract fixture", () => {
  it("parses into the NormalizedEvent shape", () => {
    const ev = JSON.parse(readFileSync(SAMPLE, "utf8")) as NormalizedEvent;
    expect(ev.kind).toBe("pull_request");
    expect(ev.repo.clone_path).toContain("gatekeep-dogfood-sample");
    expect(ev.changed_files).toEqual(["src/auth.py"]);
  });

  it("normalizer emits the contract shape (same keys as the shared fixture)", async () => {
    const sample = JSON.parse(readFileSync(SAMPLE, "utf8"));
    const ok = {
      rest: {
        pulls: {
          get: vi.fn().mockResolvedValue({ data: sample.diff }),
          listFiles: vi.fn().mockResolvedValue({ data: [{ filename: "src/auth.py" }] }),
        },
        repos: {
          getCombinedStatusForRef: vi.fn().mockResolvedValue({ data: { state: "failure" } }),
        },
        users: {
          getByUsername: vi
            .fn()
            .mockResolvedValue({ data: { created_at: "2020-01-01T00:00:00Z" } }),
        },
      },
    };
    const payload = {
      action: "opened",
      number: 7,
      pull_request: {
        number: 7,
        title: sample.title,
        body: sample.body,
        user: { login: "octocat" },
        author_association: "FIRST_TIME_CONTRIBUTOR",
        head: { sha: "abc" },
        base: {
          repo: { owner: { login: "gatekeep-dogfood" }, name: "sample", default_branch: "main" },
        },
      },
    };
    const ev = await normalizePullRequest(
      ok as any,
      payload as any,
      sample.delivery_id,
      sample.repo.clone_path,
      () => new Date("2020-01-13T00:00:00Z"),
    );
    expect(Object.keys(ev).sort()).toEqual(Object.keys(sample).sort());
  });
});
