import { readFileSync, rmSync } from "node:fs";

const manifest = JSON.parse(readFileSync("package.json", "utf8")) as {
  exports: Record<string, { import: string }>;
};
/** @spec Only declared public entry points are built; shared chunks retain class identity. */
const entrypoints = [...new Set(Object.values(manifest.exports).map(entry =>
  entry.import.replace(/^\.\/dist\//, "src/").replace(/\.js$/, ".ts")))];
rmSync("dist", { recursive: true, force: true });
const result = await Bun.build({ entrypoints, root: "src", outdir: "dist",
  target: "node", format: "esm", splitting: true, packages: "external" });
if (!result.success) throw new AggregateError(result.logs, "Toolbox build failed");
