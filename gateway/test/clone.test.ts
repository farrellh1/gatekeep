import { describe, it, expect, vi } from "vitest";
import { ensureClone, type GitRunner, type CloneRepo } from "../src/clone.js";

const repo: CloneRepo = {
  owner: "o", name: "r", default_branch: "main",
  clone_url: "https://github.com/o/r.git",
};

describe("ensureClone", () => {
  it("clones with --depth 1 on the default branch when absent", async () => {
    const calls: string[][] = [];
    const fakeGit: GitRunner = async (args) => { calls.push(args); };
    const dir = await ensureClone(repo, fakeGit, { exists: () => false });
    expect(dir).toContain("o-r");
    expect(calls[0]).toEqual(
      ["clone", "--depth", "1", "--branch", "main", "https://github.com/o/r.git", dir]);
  });

  it("fetches + hard-resets when a clone already exists", async () => {
    const calls: string[][] = [];
    const fakeGit: GitRunner = async (args) => { calls.push(args); };
    await ensureClone(repo, fakeGit, { exists: () => true });
    expect(calls[0]).toEqual(["fetch", "--depth", "1", "origin", "main"]);
    expect(calls[1]).toEqual(["reset", "--hard", "origin/main"]);
  });
});
