import type { View } from "./model";
import { renderHtml } from "./html";
import { renderText } from "./text";
export type ViewFormat = "text" | "html" | "json";
/** Serialize one prepared View. Renderers never hold an analysis context or fetch evidence. */
export function render(view: View, options: { format: ViewFormat }): string {
  switch (options.format) {
    case "html": return renderHtml(view);
    case "text": return renderText(view);
    case "json": return JSON.stringify(view, null, 2) + "\n";
    default: throw new Error(`Unsupported view format: ${options.format}`);
  }
}
