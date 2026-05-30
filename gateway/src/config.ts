import type { Octokit } from "octokit";

export async function fetchConfig(
  octokit: Octokit, target: { owner: string; repo: string },
): Promise<string | null> {
  try {
    const res = await octokit.rest.repos.getContent({ ...target, path: ".gatekeep.yml" });
    const data = res.data as { content?: string; encoding?: string };
    if (!data.content) return null;
    return Buffer.from(data.content, (data.encoding as BufferEncoding) ?? "base64").toString("utf8");
  } catch (err: any) {
    if (err?.status === 404) return null;
    throw err;
  }
}
