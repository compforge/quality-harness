import chart from "../../../../../report-kit/vendor/echarts.min.js" with { type: "text" };
import license from "../../../../../report-kit/vendor/echarts-LICENSE" with { type: "text" };
import notice from "../../../../../report-kit/vendor/echarts-NOTICE" with { type: "text" };
import reportStyle from "../../../../../report-kit/document/style.css" with { type: "text" };
import sort from "../../../../../report-kit/document/sort.js" with { type: "text" };
export { reportStyle, sort };
export function chartScript(): string {
  return "<script>/*" + (license + notice).replaceAll("*/","* /") + "*/" + chart.replace(/<\/script/gi, "<\\/script") + "</script>";
}
import tabs from "../../../../../report-kit/tabs/view.js" with { type: "text" };
import style from "../../../../../report-kit/graph/style.css" with { type: "text" };
import cytoscape from "../../../../../report-kit/vendor/cytoscape.min.js" with { type: "text" };
import elk from "../../../../../report-kit/vendor/elk.bundled.js" with { type: "text" };
import layout from "../../../../../report-kit/graph/layout.js" with { type: "text" };
import filters from "../../../../../report-kit/graph/filter.js" with { type: "text" };
import view from "../../../../../report-kit/graph/view.js" with { type: "text" };
import cytoscapeLicense from "../../../../../report-kit/vendor/cytoscape-LICENSE" with { type: "text" };
import elkLicense from "../../../../../report-kit/vendor/elk-LICENSE.md" with { type: "text" };
// One canonical browser implementation is embedded at build time; no Python/runtime file dependency.
export function graphScripts(): string {
  const script = (code: string) => `<script>${code.replace(/<\/script/gi, "<\\/script")}</script>`;
  const licensed = (code: string, license: string) => `/*\n${license.replaceAll("*/","* /")}\n*/\n${code}`;
  return `<style>${style}</style>` + [licensed(cytoscape,cytoscapeLicense),licensed(elk,elkLicense),layout,filters,view].map(script).join("\n");
}

export function tabScript(): string {
  return `<script>${tabs.replace(/<\/script/gi, "<\\/script")}</script>`;
}
