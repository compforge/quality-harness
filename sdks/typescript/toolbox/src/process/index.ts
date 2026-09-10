import { spawn } from "node:child_process";
import { Readable } from "node:stream";

export interface RuntimeProcess {
  stdout: ReadableStream<Uint8Array>;
  stderr: ReadableStream<Uint8Array>;
  exited: Promise<number>;
  kill(): void;
}

export interface SpawnProcessOptions {
  stdin?: string | Uint8Array;
  env?: NodeJS.ProcessEnv;
}

/** 使用 Node-compatible 子进程 API，不要求调用方使用 Bun runtime。 */
export function spawnProcess(argv: string[], opts?: SpawnProcessOptions): RuntimeProcess {
  if (!argv.length) throw new Error("command argv cannot be empty");

  const child = spawn(argv[0], argv.slice(1), {
    stdio: "pipe",
    env: opts?.env,
  });
  child.stdin.end(opts?.stdin);

  const exited = new Promise<number>((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code) => resolve(code ?? 1));
  });

  return {
    stdout: Readable.toWeb(child.stdout) as ReadableStream<Uint8Array>,
    stderr: Readable.toWeb(child.stderr) as ReadableStream<Uint8Array>,
    exited,
    kill: () => child.kill(),
  };
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
