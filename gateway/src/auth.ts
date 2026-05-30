import type { App, Octokit } from "octokit";

export async function installationOctokit(app: App, installationId: number): Promise<Octokit> {
  return app.getInstallationOctokit(installationId) as unknown as Octokit;
}
