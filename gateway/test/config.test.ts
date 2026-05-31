import { describe, it, expect, vi } from "vitest";
import { fetchConfig } from "../src/config.js";

const target = { owner: "o", repo: "r" };

describe("fetchConfig", () => {
  it("returns the decoded yaml text when .gatekeep.yml exists", async () => {
    const yaml = "mode: suggest-only\n";
    const ok = {
      rest: {
        repos: {
          getContent: vi.fn().mockResolvedValue({
            data: { content: Buffer.from(yaml).toString("base64"), encoding: "base64" },
          }),
        },
      },
    };
    expect(await fetchConfig(ok as any, target)).toBe(yaml);
  });

  it("returns null when the file is missing (404)", async () => {
    const ok = { rest: { repos: { getContent: vi.fn().mockRejectedValue({ status: 404 }) } } };
    expect(await fetchConfig(ok as any, target)).toBeNull();
  });
});
