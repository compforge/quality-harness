import { expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

test("toolbox sources do not depend on consumers or domain harnesses", () => {
  function inspect(directory: string) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) inspect(path);
      else if (path.endsWith(".ts")) {
        const source = readFileSync(path, "utf8");
        expect(source).not.toMatch(/from\s+["'][^"']*(?:cli\/|doctor-plugin|doctor-toolkit|command\/|(?:trace|perf|eval|e2e|trajectory)-harness)/);
      }
    }
  }
  inspect(new URL("../src", import.meta.url).pathname);
});
