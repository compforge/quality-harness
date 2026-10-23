import type { TraceView, View } from "./model";
import { renderHtml } from "./html";
import { renderTraceHtml } from "./interactive";
import { renderText } from "./text";
export type ViewFormat = "text" | "html" | "json";
/** Serialize prepared Views without fetching evidence or running analysis. */
export function render(view: View, options: { format: ViewFormat }): string;
export function render(views: readonly TraceView[], options: { format: "html" }): string;
export function render(view: View | readonly TraceView[], options: { format: ViewFormat }): string {
  // @spec Views of one trace share an HTML page; the first view is initially selected.
  if (!("scope" in view)) {
    if (options.format !== "html") throw new Error("Multiple trace views require HTML format");
    if (view.some(item => item.scope !== "trace")) throw new Error("HTML composition requires trace views");
    return renderTraceHtml(view);
  }
  switch (options.format) {
    case "html": return renderHtml(view);
    case "text": return renderText(view);
    case "json": return JSON.stringify(view, null, 2) + "\n";
    default: throw new Error(`Unsupported view format: ${options.format}`);
  }
}
