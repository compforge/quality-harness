import { readdirSync, rmSync } from "node:fs";
import { join } from "node:path";

function sources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sources(path) : path.endsWith(".ts") && !path.endsWith(".d.ts") ? [path] : [];
  });
}
// Shared chunks preserve class identity across public subpath imports. Drivers stay external
// so importing lifecycle contracts does not initialize database or Kubernetes dependencies.
rmSync("dist", { recursive: true, force: true });
const result = await Bun.build({ entrypoints: sources("src"), root: "src", outdir: "dist",
  target: "node", format: "esm", splitting: true, packages: "external" });
if (!result.success) throw new AggregateError(result.logs, "Toolbox build failed");
