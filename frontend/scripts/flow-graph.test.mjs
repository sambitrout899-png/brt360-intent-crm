import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import ts from "typescript";

const source = readFileSync(new URL("../src/flowGraph.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const module = { exports: {} };
new Function("exports", "require", "module", compiled)(module.exports, createRequire(import.meta.url), module);
const { editableFlowGraph } = module.exports;

test("previously saved journey nodes render without labels and retain their logic", () => {
  const graph = {
    nodes: [
      { id: "start", type: "start", x: 80, y: 40, config: {} },
      { id: "guard", type: "condition", x: 80, y: 160, config: { field: "record.journey", operator: "empty" } },
      { id: "complete", type: "set_field", x: 80, y: 280, config: { field: "record.journey", value: "customer" } },
    ],
    edges: [{ id: "e1", source: "guard", target: "complete", label: "true" }],
  };
  const original = structuredClone(graph);
  const editable = editableFlowGraph(graph);
  assert.deepEqual(editable.nodes.map((n) => n.label), ["Start", "Condition", "Set Field"]);
  // The canvas previously crashed here when reading label.length.
  assert.doesNotThrow(() => editable.nodes.map((n) => n.label.length > 24 ? n.label.slice(0, 23) : n.label));
  assert.deepEqual(editable.nodes.map((n) => n.config), original.nodes.map((n) => n.config));
  assert.deepEqual(editable.edges, original.edges);
  assert.deepEqual(graph, original);
  assert.deepEqual(editableFlowGraph(editable), editable);
});

test("keeps edited labels and supplies missing canvas metadata", () => {
  const graph = { nodes: [{ id: "n1", type: "notify", label: "Tell the installer" },
    { id: "n2", type: "end", label: null, x: null, y: null }], edges: [] };
  const editable = editableFlowGraph(graph);
  assert.equal(editable.nodes[0].label, "Tell the installer");
  assert.equal(editable.nodes[1].label, "End");
  assert.ok(editable.nodes.every((n) => Number.isFinite(n.x) && Number.isFinite(n.y)));
  assert.deepEqual(editable.nodes[0].config, {});
});
