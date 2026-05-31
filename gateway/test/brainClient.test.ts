import { describe, it, expect, vi, afterEach } from "vitest";
import { callBrain } from "../src/brainClient.js";
import type { NormalizedEvent } from "../src/types.js";

const event = { delivery_id: "d", kind: "pull_request", number: 1 } as unknown as NormalizedEvent;

afterEach(() => vi.restoreAllMocks());

describe("callBrain", () => {
  it("POSTs event + config_yaml to /process and returns the parsed result", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ actions: [{ action: "comment", body: "hi" }], findings: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await callBrain(event, "mode: suggest-only", "http://brain");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://brain/process");
    expect(JSON.parse(init.body)).toEqual({ event, config_yaml: "mode: suggest-only" });
    expect(result.actions[0].body).toBe("hi");
  });

  it("throws on a non-200 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 422, text: async () => "bad" }),
    );
    await expect(callBrain(event, null, "http://brain")).rejects.toThrow(/422/);
  });
});
