import { strFromU8, unzipSync } from "fflate";

export function archiveEntries(html: string): Record<string, Uint8Array> {
  const payload = html.match(/<template id="trace-archive">([A-Za-z0-9+/=]+)<\/template>/)?.[1];
  if (!payload) throw new Error("Missing trace archive");
  return unzipSync(Buffer.from(payload, "base64"));
}
export function archiveContents(html: string): string {
  return Object.values(archiveEntries(html)).map(value => strFromU8(value)).join("\n");
}
export function traceTrees(html: string): any {
  const entries = archiveEntries(html);
  const index = JSON.parse(strFromU8(entries["index.json"]!));
  const hydrate = (node: any): any => ({ ...node, ...JSON.parse(strFromU8(entries[node.payload]!)), children: node.children.map(hydrate) });
  return Object.fromEntries(Object.entries(index.trees).map(([key, tree]: [string, any]) => [key, { roots: tree.roots.map(hydrate) }]));
}
