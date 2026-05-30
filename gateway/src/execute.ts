import type { Octokit } from "octokit";
import type { Action } from "./types.js";

export interface Target { owner: string; repo: string; issue_number: number; }

export async function execute(
  octokit: Octokit, target: Target, actions: Action[],
): Promise<void> {
  for (const action of actions) {
    switch (action.action) {
      case "comment":
        await octokit.rest.issues.createComment({ ...target, body: action.body! });
        break;
      case "label":
        await octokit.rest.issues.addLabels({ ...target, labels: [action.body!] });
        break;
      case "close":
        await octokit.rest.issues.update({ ...target, state: "closed" });
        break;
    }
  }
}
