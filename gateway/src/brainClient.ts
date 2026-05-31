import type { NormalizedEvent, BrainResult } from "./types.js";

export async function callBrain(
  event: NormalizedEvent,
  configYaml: string | null,
  brainUrl: string,
): Promise<BrainResult> {
  const res = await fetch(`${brainUrl}/process`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ event, config_yaml: configYaml }),
  });
  if (!res.ok) {
    throw new Error(`Brain returned ${res.status}: ${await res.text()}`);
  }
  return (await res.json()) as BrainResult;
}
