import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import path from "node:path";

const exec = promisify(execFile);

export interface CloneRepo {
  owner: string;
  name: string;
  default_branch: string;
  clone_url: string;
}

export type GitRunner = (args: string[], cwd?: string) => Promise<void>;
interface Fs {
  exists: (p: string) => boolean;
}

const realGit: GitRunner = async (args, cwd) => {
  await exec("git", args, { cwd });
};
const realFs: Fs = { exists: existsSync };

const ROOT = "/tmp/gatekeep/clones";
const locks = new Map<string, Promise<void>>();

export async function ensureClone(
  repo: CloneRepo,
  git: GitRunner = realGit,
  fs: Fs = realFs,
): Promise<string> {
  const dir = path.join(ROOT, `${repo.owner}-${repo.name}`);
  const prev = locks.get(dir) ?? Promise.resolve();
  let release!: () => void;
  const mine = new Promise<void>((r) => {
    release = r;
  });
  locks.set(
    dir,
    prev.then(() => mine),
  );
  await prev;
  try {
    await mkdir(ROOT, { recursive: true });
    if (!fs.exists(path.join(dir, ".git"))) {
      await git(["clone", "--depth", "1", "--branch", repo.default_branch, repo.clone_url, dir]);
    } else {
      await git(["fetch", "--depth", "1", "origin", repo.default_branch], dir);
      await git(["reset", "--hard", `origin/${repo.default_branch}`], dir);
    }
    return dir;
  } finally {
    release();
  }
}
