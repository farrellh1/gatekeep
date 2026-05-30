import { describe, it, expect, vi } from "vitest";
import { execute } from "../src/execute.js";
import type { Action } from "../src/types.js";

function fakeOctokit() {
  return {
    rest: {
      issues: {
        createComment: vi.fn().mockResolvedValue({}),
        addLabels: vi.fn().mockResolvedValue({}),
        update: vi.fn().mockResolvedValue({}),
      },
    },
  };
}
const target = { owner: "o", repo: "r", issue_number: 5 };

describe("execute", () => {
  it("maps comment → createComment with the body", async () => {
    const ok = fakeOctokit();
    await execute(ok as any, target, [{ action: "comment", body: "polite note" }]);
    expect(ok.rest.issues.createComment).toHaveBeenCalledWith({ ...target, body: "polite note" });
  });

  it("maps label → addLabels with the body as the label name", async () => {
    const ok = fakeOctokit();
    await execute(ok as any, target, [{ action: "label", body: "possible-slop" }]);
    expect(ok.rest.issues.addLabels).toHaveBeenCalledWith({ ...target, labels: ["possible-slop"] });
  });

  it("maps close → update(state=closed)", async () => {
    const ok = fakeOctokit();
    await execute(ok as any, target, [{ action: "close" }]);
    expect(ok.rest.issues.update).toHaveBeenCalledWith({ ...target, state: "closed" });
  });

  it("executes an empty action list as a no-op (legit case)", async () => {
    const ok = fakeOctokit();
    await execute(ok as any, target, []);
    expect(ok.rest.issues.createComment).not.toHaveBeenCalled();
    expect(ok.rest.issues.update).not.toHaveBeenCalled();
  });

  it("runs every action in order", async () => {
    const ok = fakeOctokit();
    const actions: Action[] = [
      { action: "comment", body: "c" },
      { action: "label", body: "possible-slop" },
    ];
    await execute(ok as any, target, actions);
    expect(ok.rest.issues.createComment).toHaveBeenCalledTimes(1);
    expect(ok.rest.issues.addLabels).toHaveBeenCalledTimes(1);
  });
});
