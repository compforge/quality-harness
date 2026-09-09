/** Project HTTP sequence findings into Group operations; never reinterpret raw spans. */
import { formatMs } from "../kinds/base";
import type { Finding, Node } from "../model/node";
import type { ViewTree } from "../model/viewtree";
import type { ChildOp } from "./facet";

type Group = Extract<ChildOp, { type: "group" }>;

export function httpCallGroups(view: ViewTree, findings: Readonly<Record<string, readonly Finding[]>>): Map<string, Group> {
  const groups = new Map<string, Group>();
  for (const finding of Object.values(findings).flat()) {
    if (finding.source !== "http_serial_same_api") continue;
    const data = finding.data ?? {};
    const spanIds = (data.span_ids ?? []) as string[];
    const members = spanIds.map((id) => view.by_span.get(id)).filter((node): node is Node => node !== undefined);
    // Older/custom IR may fuse or omit requests. Never manufacture members, re-parent,
    // or include unrelated siblings merely to draw a sequence.
    if (members.length < 2 || new Set(members.map((node) => node.node_id)).size !== spanIds.length) continue;
    const parentId = members[0]!.parent_node_id;
    if (members.some((node) => node.parent_node_id !== parentId)) continue;
    const parent = parentId ? view.by_id.get(parentId) : undefined;
    const siblings = parent ? view.children(parent) : view.roots;
    const start = siblings.indexOf(members[0]!);
    if (members.some((node, i) => siblings[start + i] !== node)) continue;
    groups.set(members[0]!.node_id, {
      type: "group", nodes: members,
      label: `⚠ ${data.method} ${data.target}${data.route} ×${data.count}`,
      brief: [
        { label: "wall-clock", value: formatMs(data.wall_ms), emphasis: "strong" },
        { label: "HTTP", value: formatMs(data.http_total_ms) },
        { label: "gap", value: formatMs(data.gap_ms), emphasis: "strong" },
      ],
    });
  }
  return groups;
}

export function groupHttpCalls(ops: ChildOp[], groups: Map<string, Group>): ChildOp[] {
  const output: ChildOp[] = [];
  for (let i = 0; i < ops.length;) {
    const op = ops[i]!;
    const group = op.type === "expand" || op.type === "fold" ? groups.get(op.node.node_id) : undefined;
    if (group) {
      const segment = ops.slice(i, i + group.nodes.length);
      // Respect explicit custom facet groups, hidden nodes and summaries.
      if (segment.length === group.nodes.length && segment.every((item, index) =>
        (item.type === "expand" || item.type === "fold") && item.node.node_id === group.nodes[index]!.node_id
      )) {
        output.push(group);
        i += group.nodes.length;
        continue;
      }
    }
    output.push(op);
    i++;
  }
  return output;
}

/** Collect adjacent HTTP rows by caller service, retaining nested API groups and order. */
export function groupHttpServices(ops: ChildOp[]): ChildOp[] {
  const output: ChildOp[] = [];
  let pending: ChildOp[] = [];
  let members: Node[] = [];
  const flush = () => {
    if (members.length >= 2) {
      output.push({ type: "group", nodes: members, label: members[0]!.service ?? "?", collapsed: false, children: pending });
    } else output.push(...pending);
    pending = [];
    members = [];
  };
  for (const op of ops) {
    const nodes = op.type === "expand" || op.type === "fold" ? [op.node]
      : op.type === "group" && op.children === undefined ? op.nodes : [];
    const eligible = nodes.length > 0 && nodes.every((node) => node.kind === "http" && node.service === nodes[0]!.service);
    if (!eligible) { flush(); output.push(op); continue; }
    if (members.length && members[0]!.service !== nodes[0]!.service) flush();
    pending.push(op);
    members.push(...nodes);
  }
  flush();
  return output;
}
