import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { createRequire } from "node:module";
import ts from "typescript";

const source = readFileSync(new URL("../src/pages/customer360/workspacePresentation.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const module = { exports: {} };
new Function("exports", "require", "module", compiled)(module.exports, createRequire(import.meta.url), module);
const { panesForTab, coveredRecords, workspaceCoverage, componentVisible, workspaceTheme, DEFAULT_WORKSPACE_THEME } = module.exports;

test("audience exclusions cannot resurrect matching native lists", () => {
  const configured = { id: "account-assets", domain: "custom", widgets: [{ type: "records", metric: { sections: ["service_accounts"] } }] };
  const view = { layout: { panes: [configured] }, panes: [], components: { orders: false, sales: false, account_information: false } };
  const summary = workspaceCoverage(view, "overview");
  assert.ok(summary.has("service_accounts"));
  assert.ok(summary.has("orders"));
  assert.ok(summary.has("account_information"));
  assert.ok(summary.has("leads") && summary.has("opportunities") && summary.has("quotes"));
  assert.ok(!workspaceCoverage(view, "services").has("service_accounts"));
  assert.equal(componentVisible(view, "orders"), false);
  assert.equal(componentVisible(view, "billing"), true);
  assert.equal(componentVisible(null, "orders"), true);
  assert.equal(workspaceCoverage(null, "overview").size, 0);
});

test("published widgets keep their order and content in their configured domain tabs", () => {
  const view = { panes: [
    { id: "first", domain: "custom", width: "third", collapsed: true, widgets: [{ type: "kpi", dataset: "orders", metric: {} }] },
    { id: "assets", domain: "inventory", widgets: [{ type: "records", metric: { sections: ["service_accounts"] } }, { type: "table", dataset: "assets", metric: {} }] },
    { id: "sales", domain: "sales", widgets: [{ type: "table", dataset: "opportunities", metric: {} }] },
    { id: "last", domain: "custom", widgets: [{ type: "insights", metric: {} }] },
  ] };
  const original = structuredClone(view);
  assert.deepEqual(panesForTab(view, "overview"), [view.panes[0], view.panes[3]]);
  assert.deepEqual(panesForTab(view, "services"), [view.panes[1]]);
  assert.deepEqual(panesForTab(view, "sales"), [view.panes[2]]);
  assert.deepEqual(view, original);
  assert.equal(panesForTab(view, "services")[0].widgets.length, 2);
});

test("configured lists replace matching defaults while charts leave operational lists available", () => {
  const pane = { widgets: [{ type: "bar", dataset: "orders", metric: {} }, { type: "kpi", dataset: "tickets", metric: {} }] };
  assert.equal(coveredRecords([pane]).size, 0);
  pane.widgets.push({ type: "table", dataset: "assets", metric: {} });
  assert.ok(coveredRecords([pane]).has("service_accounts"));
  assert.ok(!coveredRecords([pane]).has("orders"));
  pane.widgets.push({ type: "records", metric: { sections: ["leads", "quotes"] } });
  assert.ok(coveredRecords([pane]).has("leads"));
  assert.ok(coveredRecords([pane]).has("quotes"));
  assert.ok(!coveredRecords([pane]).has("opportunities"));
});

test("the entire workspace uses the published theme, card, spacing and header settings", () => {
  const published = { ...DEFAULT_WORKSPACE_THEME, bg: "#ffffff", text: "#102030", muted: "#667788", accent: "#ec4899", surface: "rgba(255,255,255,0.7)", border: "#dddddd", hero: "linear-gradient(90deg,#fff,#eee)", density: "compact", header_style: "compact", card_style: "solid" };
  const style = workspaceTheme(published);
  assert.equal(style["--c-bg"], published.bg);
  assert.equal(style["--c-text"], published.text);
  assert.equal(style["--c-accent"], published.accent);
  assert.equal(style["--c-hero"], published.hero);
  assert.equal(style["--account-card-bg"], "rgba(255,255,255,1)");
  assert.equal(style["--account-gap"], "12px");
  assert.equal(style["--account-header-padding"], "14px 18px");
  assert.equal(workspaceTheme({ ...published, card_style: "outline" })["--account-card-bg"], "transparent");
  assert.notEqual(workspaceTheme({ ...published, density: "comfortable" })["--account-gap"], style["--account-gap"]);
});
