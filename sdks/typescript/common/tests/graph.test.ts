import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { Graph, NodeKind, RelationKind, createRelation, operationNodeId, serviceNodeId, type Node, type GraphSnapshot } from "../src/index.js";

const fixture = JSON.parse(readFileSync(new URL("../../../../conformance/common/graphs.json", import.meta.url), "utf8")) as {
  graph: GraphSnapshot; root: string; reachable: string[];
  ids: { service: string; operation: string; service_id: string; operation_id: string }[];
  invalid: { name: string; graph: unknown }[];
};

test("shared downstream and cycles roundtrip and traverse through public exports", () => {
  const graph = Graph.fromJSON(fixture.graph);
  expect(graph.toJSON()).toEqual(fixture.graph);
  expect(Graph.fromJSON(JSON.parse(JSON.stringify(graph))).toJSON()).toEqual(fixture.graph);
  expect(graph.reachableFrom(fixture.root, "calls").map(n => n.id)).toEqual(fixture.reachable);
  expect(graph.relationsTo("operation:d/api4", "calls")).toHaveLength(2);
  expect(graph.relationsFrom(fixture.root, "calls")).toHaveLength(2);
  expect(graph.node("missing")).toBeUndefined();
  expect(graph.relationsTo("missing")).toEqual([]);
  expect(() => graph.reachableFrom("missing")).toThrow("unknown root");
});

test("subgraph retains owners without expanding unrelated service APIs", () => {
  const graph = Graph.fromJSON(fixture.graph);
  const subset = graph.subgraph(graph.reachableFrom("operation:a/api1", "calls").map(n => n.id));
  expect(subset.nodes.map(n => n.id)).toEqual(["service:a", "service:d", "operation:a/api1", "operation:d/api4"]);
  expect(subset.source).toBe(graph.source);
  expect(subset.version).toBe(graph.version);
  expect(subset.environment).toBe(graph.environment);
  expect(Graph.fromJSON(subset.toJSON()).toJSON()).toEqual(subset.toJSON());
  expect(graph.subgraph([]).nodes).toEqual([]);
  expect(() => graph.subgraph(["missing"])).toThrow("unknown node");
});

for (const item of fixture.invalid) {
  test(`invalid topology: ${item.name}`, () => { expect(() => Graph.fromJSON(item.graph)).toThrow(); });
}

test("name-derived IDs share escaping rules and service qualification", () => {
  for (const item of fixture.ids) {
    expect(serviceNodeId(item.service)).toBe(item.service_id);
    expect(operationNodeId(item.service, item.operation)).toBe(item.operation_id);
  }
  expect(operationNodeId("a/b", "c")).not.toBe(operationNodeId("a", "b/c"));
  expect(operationNodeId("a", "same")).not.toBe(operationNodeId("b", "same"));
  expect(() => operationNodeId("a", "")).toThrow();
});

test("snapshot detaches input and allows consumer kinds", () => {
  const nodes: Node[] = [{ id: "model:x", kind: "model" }];
  const relations = [createRelation("model:x", "model:x", "uses")];
  const graph = new Graph({ environment: "qa", nodes, relations });
  nodes[0] = { id: "changed", kind: "changed" };
  relations.length = 0;
  expect(graph.node("model:x")?.kind).toBe("model");
  expect(graph.reachableFrom("model:x")).toHaveLength(1);
  expect(Object.isFrozen(graph)).toBe(true);
  expect(Object.isFrozen(graph.nodes[0])).toBe(true);
  expect(Graph.fromJSON(graph.toJSON()).toJSON()).toEqual(graph.toJSON());
  expect(createRelation("a,b", "c", "uses").id).not.toBe(createRelation("a", "b,c", "uses").id);
});

test("built ESM package exports the graph API", async () => {
  const built = await import(new URL("../dist/index.js", import.meta.url).href) as typeof import("../src/index.js");
  expect(built.Graph.fromJSON(fixture.graph).toJSON()).toEqual(fixture.graph);
  expect(built.NodeKind.Service).toBe("service");
  expect(built.RelationKind.Calls).toBe("calls");
});


test("open kind constants and extensions retain wire identity and validation", () => {
  const customNode: NodeKind = "model";
  const customRelation: RelationKind = "uses";
  const nodes: Node[] = [{ id: "s", kind: NodeKind.Service }, { id: "o", kind: NodeKind.Operation }, { id: "m", kind: customNode }];
  const relations = [createRelation("o", "s", RelationKind.BelongsTo), createRelation("o", "o", RelationKind.Calls), createRelation("o", "m", customRelation)];
  const graph = new Graph({ environment: "qa", nodes, relations });
  const loaded = Graph.fromJSON(JSON.parse(JSON.stringify(graph)));
  expect(loaded.toJSON()).toEqual(graph.toJSON());
  expect(loaded.node("s")?.kind).toBe(NodeKind.Service);
  expect(loaded.node("m")?.kind).toBe("model");
  expect(loaded.relationsFrom("o", customRelation)).toEqual([relations[2]]);
  expect(graph.reachableFrom("o", RelationKind.Calls)).toEqual([nodes[1]]);
  expect(createRelation("o", "o", RelationKind.Calls).id).toBe(createRelation("o", "o", "calls").id);
  expect(Object.isFrozen(NodeKind)).toBe(true);
  expect(Object.isFrozen(RelationKind)).toBe(true);
  expect(() => new Graph({ environment: "qa", nodes, relations: [...relations, createRelation("s", "o", RelationKind.Calls)] })).toThrow("calls requires");
});
