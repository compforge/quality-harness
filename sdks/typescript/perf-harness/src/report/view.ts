import type { ArmRun, RequestStats } from "../model";
export function escape(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
export function table(
  headers: string[],
  rows: unknown[][],
  tips: Record<string, string> = {},
): string {
  const header = headers
    .map(
      (h) =>
        `<th onclick="rkSort(this)"${tips[h] ? ` class="tip" data-tip="${escape(tips[h])}"` : ""}>${escape(h)}</th>`,
    )
    .join("");
  const body = rows
    .map(
      (row) =>
        "<tr>" +
        row.map((v) => `<td data-v="${escape(v)}">${escape(v)}</td>`).join("") +
        "</tr>",
    )
    .join("");
  return `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}
export function section(title: string, body: string): string {
  return `<details open><summary>${escape(title)}</summary>${body}</details>`;
}
export function full(a: ArmRun) {
  return a.windows.find((w) => w.kind === "full");
}
export function drop(s: RequestStats) {
  return s.arrived ? s.n_dropped / s.arrived : 0;
}
