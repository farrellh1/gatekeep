import { describe, it, expect } from "vitest";
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
});
