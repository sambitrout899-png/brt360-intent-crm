import { useState } from "react";
import { c360CreateConnector, c360DeleteConnector, c360MapFields, c360TestConnector, c360UpdateConnector } from "../../api/client";
import type { C360Connector, C360ConnectorDraft, C360ConnectorInput, C360Endpoint, C360MappingSuggestion, C360Studio, C360TestResult } from "../../api/c360Types";

/** External systems: billing, service desk, network OSS, inventory. Connect
 * one with an address and credentials, test it against a real customer and
 * let AI map its field names onto ours - or start with a demo system. */

const DOMAIN_ICON: Record<string, string> = { billing: "💳", service: "🛠️", network: "📡", inventory: "🗄️", sales: "💼" };

function blank(studio: C360Studio, domain = "billing"): C360ConnectorInput {
  return {
    name: "",
    domain,
    kind: "rest",
    base_url: "",
    auth_type: "bearer",
    auth_header: "",
    secret: "",
    endpoints: Object.fromEntries(
      (studio.domain_datasets[domain] ?? []).map((d) => [d, { path: studio.default_paths[d] ?? "/", records_path: "", field_map: {} }])
    ),
    is_active: true,
  };
}

function fromConnector(c: C360Connector): C360ConnectorInput {
  return { name: c.name, domain: c.domain, kind: c.kind, base_url: c.base_url, auth_type: c.auth_type, auth_header: c.auth_header, secret: null, endpoints: c.endpoints, is_active: c.is_active };
}

function MappingEditor({
  studio,
  dataset,
  endpoint,
  theirFields,
  onChange,
}: {
  studio: C360Studio;
  dataset: string;
  endpoint: C360Endpoint;
  theirFields: string[];
  onChange: (e: C360Endpoint) => void;
}) {
  const ds = studio.catalog.datasets.find((d) => d.key === dataset);
  const listId = `their-${dataset}`;
  return (
    <div className="space-y-1">
      <datalist id={listId}>
        {theirFields.map((f) => (
          <option key={f} value={f} />
        ))}
      </datalist>
      {ds?.fields.map((f) => (
        <label key={f.key} className="flex items-center gap-2 text-[11px]">
          <span className="w-28 shrink-0 truncate text-slate-400" title={f.key}>
            {f.label}
          </span>
          <span className="text-slate-600">←</span>
          <input
            list={listId}
            value={endpoint.field_map[f.key] ?? ""}
            placeholder={f.key}
            onChange={(e) => {
              const field_map = { ...endpoint.field_map };
              if (e.target.value.trim()) field_map[f.key] = e.target.value.trim();
              else delete field_map[f.key];
              onChange({ ...endpoint, field_map });
            }}
            className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 font-mono text-[11px] text-slate-200"
          />
        </label>
      ))}
    </div>
  );
}

export default function SourcesTab({
  studio,
  pending,
  setPending,
  partyId,
  onChanged,
  onAgent,
}: {
  studio: C360Studio;
  pending: C360ConnectorDraft[];
  setPending: (next: C360ConnectorDraft[]) => void;
  partyId: number | null;
  onChanged: () => void;
  onAgent: (instruction: string) => void;
}) {
  const [editing, setEditing] = useState<{ id: number | null; form: C360ConnectorInput } | null>(null);
  const [dataset, setDataset] = useState<string>("");
  const [sample, setSample] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [test, setTest] = useState<C360TestResult | null>(null);
  const [mapping, setMapping] = useState<C360MappingSuggestion | null>(null);

  const dsLabel = (key: string) => studio.catalog.datasets.find((d) => d.key === key)?.label ?? key;

  function startEdit(id: number | null, form: C360ConnectorInput) {
    setEditing({ id, form });
    setDataset(Object.keys(form.endpoints)[0] ?? "");
    setTest(null);
    setMapping(null);
    setSample("");
    setError("");
  }

  const form = editing?.form;
  const setForm = (patch: Partial<C360ConnectorInput>) => editing && setEditing({ ...editing, form: { ...editing.form, ...patch } });
  const endpoint = form && dataset ? form.endpoints[dataset] : undefined;
  const setEndpoint = (e: C360Endpoint) => form && setForm({ endpoints: { ...form.endpoints, [dataset]: e } });

  async function save(): Promise<number | null> {
    if (!editing) return null;
    setBusy("save");
    setError("");
    try {
      const saved = editing.id ? await c360UpdateConnector(editing.id, editing.form) : await c360CreateConnector(editing.form);
      setEditing({ id: saved.id, form: { ...fromConnector(saved) } });
      onChanged();
      return saved.id;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
      return null;
    } finally {
      setBusy("");
    }
  }

  async function runTest() {
    const id = await save();
    if (!id) return;
    setBusy("test");
    try {
      const result = await c360TestConnector(id, dataset || undefined, partyId ?? undefined);
      setTest(result);
      if (result.suggestion) setMapping(result.suggestion);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Test failed");
    } finally {
      setBusy("");
    }
  }

  async function autoMap() {
    if (!sample.trim() || !dataset) return;
    setBusy("map");
    setError("");
    try {
      setMapping(await c360MapFields(dataset, sample));
    } catch (e) {
      setError(e instanceof Error ? e.message.replace(/^\d+ [^:]*: /, "") : "Could not read that sample");
    } finally {
      setBusy("");
    }
  }

  function applyMapping() {
    if (!mapping || !endpoint) return;
    setEndpoint({ ...endpoint, records_path: mapping.records_path, field_map: mapping.field_map });
  }

  async function remove(c: C360Connector) {
    await c360DeleteConnector(c.id);
    if (editing?.id === c.id) setEditing(null);
    onChanged();
  }

  const unconnected = ["billing", "service", "network", "inventory"].filter(
    (d) => !studio.connectors.some((c) => c.domain === d) && !pending.some((p) => p.domain === d)
  );

  return (
    <div className="space-y-4">
      {unconnected.length > 0 && (
        <div className="rounded-xl border border-violet-500/30 bg-violet-500/10 p-3">
          <p className="text-xs font-semibold text-violet-200">✨ No real endpoints yet? Try the page with demo systems</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {unconnected.map((d) => (
              <button key={d} onClick={() => onAgent(`connect our ${d} system`)} className="rounded-md bg-violet-600/70 px-2 py-1 text-[11px] font-bold text-white hover:bg-violet-600">
                {DOMAIN_ICON[d]} Demo {d}
              </button>
            ))}
            {unconnected.length > 1 && (
              <button onClick={() => onAgent("connect all the respective systems")} className="rounded-md border border-violet-400/50 px-2 py-1 text-[11px] font-bold text-violet-100 hover:bg-violet-500/20">
                All of them
              </button>
            )}
          </div>
        </div>
      )}

      <section className="space-y-1.5">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Connected systems</h3>
          <button onClick={() => startEdit(null, blank(studio))} className="text-xs font-semibold text-sky-400 hover:text-sky-300">
            + Connect a system
          </button>
        </div>
        {pending.map((p) => (
          <div key={p.key} className="flex items-center gap-2 rounded-lg border border-dashed border-amber-500/40 bg-amber-500/5 px-2.5 py-2 text-xs">
            <span>{DOMAIN_ICON[p.domain]}</span>
            <span className="min-w-0 flex-1 truncate font-semibold text-slate-200">{p.name}</span>
            <span className="text-[10px] text-amber-300">connects on save</span>
            <button onClick={() => setPending(pending.filter((x) => x.key !== p.key))} className="text-slate-500 hover:text-rose-400">
              ✕
            </button>
          </div>
        ))}
        {studio.connectors.map((c) => (
          <div key={c.id} className={`flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs ${editing?.id === c.id ? "border-sky-500 bg-sky-500/5" : "border-slate-700 bg-slate-900/70"}`}>
            <span>{DOMAIN_ICON[c.domain]}</span>
            <button onClick={() => startEdit(c.id, fromConnector(c))} className="min-w-0 flex-1 text-left">
              <span className="block truncate font-semibold text-slate-100">{c.name}</span>
              <span className="block truncate text-[10px] text-slate-500">
                {c.kind === "demo" ? "demo data" : c.base_url || "no address yet"} · {Object.keys(c.endpoints).map(dsLabel).join(", ")}
              </span>
            </button>
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${!c.is_active ? "bg-slate-600" : c.last_test_ok === false ? "bg-rose-400" : c.last_test_ok ? "bg-emerald-400" : "bg-slate-400"}`}
              title={c.last_test_message || (c.is_active ? "Not tested yet" : "Switched off")}
            />
            <button onClick={() => remove(c)} className="text-slate-500 hover:text-rose-400" title="Disconnect">
              ✕
            </button>
          </div>
        ))}
        {studio.connectors.length === 0 && pending.length === 0 && <p className="text-xs text-slate-500">Nothing connected yet - BRT 360's own data is always available.</p>}
      </section>

      {form && (
        <section className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/80 p-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-bold text-slate-100">{editing?.id ? "Edit system" : "Connect a system"}</h3>
            <button onClick={() => setEditing(null)} className="text-xs text-slate-500 hover:text-slate-300">
              Close
            </button>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input value={form.name} onChange={(e) => setForm({ name: e.target.value })} placeholder="Name, e.g. Vi BSS" className="col-span-2 rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100" />
            <select
              value={form.domain}
              onChange={(e) => {
                const fresh = blank(studio, e.target.value);
                setForm({ domain: e.target.value, endpoints: fresh.endpoints });
                setDataset(Object.keys(fresh.endpoints)[0] ?? "");
              }}
              className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200"
            >
              {Object.keys(studio.domain_datasets).map((d) => (
                <option key={d} value={d}>
                  {DOMAIN_ICON[d]} {d}
                </option>
              ))}
            </select>
            <select value={form.kind} onChange={(e) => setForm({ kind: e.target.value as "rest" | "demo" })} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200">
              <option value="rest">REST API</option>
              <option value="demo">Demo data</option>
            </select>
          </div>

          {form.kind === "rest" && (
            <div className="space-y-2">
              <input value={form.base_url} onChange={(e) => setForm({ base_url: e.target.value })} placeholder="https://bss.example.com/api" className="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-100" />
              <div className="grid grid-cols-2 gap-2">
                <select value={form.auth_type} onChange={(e) => setForm({ auth_type: e.target.value })} className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200">
                  <option value="none">No auth</option>
                  <option value="bearer">Bearer token</option>
                  <option value="api_key">API key header</option>
                  <option value="basic">Basic (user:password)</option>
                </select>
                {form.auth_type === "api_key" ? (
                  <input value={form.auth_header} onChange={(e) => setForm({ auth_header: e.target.value })} placeholder="X-API-Key" className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-xs text-slate-100" />
                ) : (
                  <span />
                )}
                {form.auth_type !== "none" && (
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={form.secret ?? ""}
                    onChange={(e) => setForm({ secret: e.target.value })}
                    placeholder={
                      editing?.id && studio.connectors.find((c) => c.id === editing.id)?.secret_set
                        ? `Saved (${studio.connectors.find((c) => c.id === editing.id)?.secret_hint}) - type to replace`
                        : form.auth_type === "basic"
                          ? "user:password"
                          : "Token / key"
                    }
                    className="col-span-2 rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-100"
                  />
                )}
              </div>
            </div>
          )}

          <div>
            <div className="mb-1.5 flex flex-wrap gap-1">
              {(studio.domain_datasets[form.domain] ?? []).map((d) => {
                const on = !!form.endpoints[d];
                return (
                  <button
                    key={d}
                    onClick={() => {
                      if (on && dataset === d) {
                        const endpoints = { ...form.endpoints };
                        delete endpoints[d];
                        setForm({ endpoints });
                        setDataset(Object.keys(endpoints)[0] ?? "");
                      } else {
                        if (!on) setForm({ endpoints: { ...form.endpoints, [d]: { path: studio.default_paths[d] ?? "/", records_path: "", field_map: {} } } });
                        setDataset(d);
                      }
                    }}
                    className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${dataset === d ? "bg-sky-600 text-white" : on ? "bg-slate-700 text-slate-200" : "border border-dashed border-slate-700 text-slate-500"}`}
                    title={on ? (dataset === d ? "Click again to stop using this dataset" : "Edit") : "Add this dataset"}
                  >
                    {on ? "" : "+ "}
                    {dsLabel(d)}
                  </button>
                );
              })}
            </div>
            {endpoint && form.kind === "rest" && (
              <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/60 p-2">
                <label className="block text-[11px] text-slate-400">
                  Endpoint for one customer
                  <input
                    value={endpoint.path}
                    onChange={(e) => setEndpoint({ ...endpoint, path: e.target.value })}
                    className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-1 font-mono text-[11px] text-slate-100"
                  />
                  <span className="text-[10px] text-slate-500">Use {"{party_id}"}, {"{customer_name}"} or {"{email}"} for the customer.</span>
                </label>
                <label className="block text-[11px] text-slate-400">
                  Where the records are in the answer
                  <input
                    value={endpoint.records_path}
                    onChange={(e) => setEndpoint({ ...endpoint, records_path: e.target.value })}
                    placeholder="auto-detect (e.g. data.items)"
                    className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-1 font-mono text-[11px] text-slate-100"
                  />
                </label>
                <details className="rounded border border-slate-800 p-1.5">
                  <summary className="cursor-pointer text-[11px] font-semibold text-slate-300">Field mapping ({Object.keys(endpoint.field_map).length} set)</summary>
                  <div className="mt-2">
                    <MappingEditor studio={studio} dataset={dataset} endpoint={endpoint} theirFields={mapping?.their_fields ?? []} onChange={setEndpoint} />
                  </div>
                </details>
                <div className="space-y-1">
                  <textarea
                    value={sample}
                    onChange={(e) => setSample(e.target.value)}
                    rows={3}
                    placeholder='Paste one answer from the system, e.g. {"items":[{"InvoiceNo":"A-1","Total":1200}]}'
                    className="w-full rounded border border-slate-700 bg-slate-950 px-1.5 py-1 font-mono text-[11px] text-slate-200"
                  />
                  <div className="flex gap-1.5">
                    <button disabled={!sample.trim() || busy !== ""} onClick={autoMap} className="rounded-md bg-violet-600 px-2 py-1 text-[11px] font-bold text-white disabled:opacity-40">
                      {busy === "map" ? "Mapping…" : "✨ Map fields from sample"}
                    </button>
                    <button disabled={busy !== "" || !form.name.trim()} onClick={runTest} className="rounded-md border border-sky-500/50 px-2 py-1 text-[11px] font-bold text-sky-300 disabled:opacity-40">
                      {busy === "test" ? "Calling…" : "⚡ Save & test live"}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {test && (
            <div className={`rounded-lg border p-2 text-[11px] ${test.ok ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-200" : "border-rose-500/30 bg-rose-500/10 text-rose-200"}`}>
              <p className="font-semibold">
                {test.ok ? "✓" : "✕"} {test.message}
              </p>
            </div>
          )}
          {mapping && (
            <div className="space-y-1.5 rounded-lg border border-violet-500/30 bg-violet-500/5 p-2 text-[11px]">
              <p className="font-semibold text-violet-200">
                ✨ {Object.keys(mapping.mapping).length} of {Object.keys(mapping.mapping).length + mapping.unmapped.length} fields matched
                {mapping.records_path && ` · records at “${mapping.records_path}”`} · {mapping.records_found} record{mapping.records_found === 1 ? "" : "s"}
              </p>
              <ul className="space-y-0.5">
                {Object.entries(mapping.mapping).map(([ours, m]) => (
                  <li key={ours} className="flex gap-1.5 text-slate-300">
                    <span className="font-mono text-violet-200">{m.source}</span>→<span>{ours}</span>
                    <span className={`ml-auto ${m.confidence >= 0.85 ? "text-emerald-400" : "text-amber-400"}`}>{Math.round(m.confidence * 100)}%</span>
                  </li>
                ))}
              </ul>
              {mapping.unmapped.length > 0 && <p className="text-slate-500">Not found: {mapping.unmapped.join(", ")}</p>}
              <button onClick={applyMapping} className="rounded-md bg-violet-600 px-2 py-1 font-bold text-white">
                Use this mapping
              </button>
            </div>
          )}

          {error && <p className="text-[11px] text-rose-300">{error}</p>}
          <div className="flex items-center gap-2 border-t border-slate-800 pt-2">
            <label className="flex items-center gap-1.5 text-[11px] text-slate-400">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ is_active: e.target.checked })} /> Active
            </label>
            <button disabled={busy !== "" || !form.name.trim() || Object.keys(form.endpoints).length === 0} onClick={save} className="ml-auto rounded-md bg-sky-600 px-3 py-1.5 text-xs font-bold text-white disabled:opacity-40">
              {busy === "save" ? "Saving…" : "Save system"}
            </button>
          </div>
          <p className="text-[10px] text-slate-500">Systems are saved straight away; choose which widgets read from them in Layout (or ask the AI).</p>
        </section>
      )}
    </div>
  );
}
