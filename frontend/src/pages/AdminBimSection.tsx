import { useCallback, useEffect, useState } from "react";
import {
  bimEntities,
  bimInterfaces,
  bimJobs,
  bimLoad,
  bimRecord,
  bimTemplate,
  reserveBrtIds,
  saveBimInterface,
  type BimEntity,
  type BimInterface,
  type BimJob,
} from "../api/client";
import { INPUT_CLS } from "../components/intent/intentUi";

const STATUS_CLS: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-300",
  completed_with_errors: "bg-amber-500/15 text-amber-300",
  failed: "bg-red-500/15 text-red-300",
  running: "bg-sky-500/15 text-sky-300",
};

function download(name: string, content: string, type = "text/csv") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");

function JobView({ job }: { job: BimJob }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-slate-800 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_CLS[job.status] ?? ""}`}>{job.status.replace(/_/g, " ")}</span>
        {job.dry_run && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">dry run - nothing kept</span>}
        <span className="text-slate-200">
          {job.entity} · {job.channel === "api" ? "API" : job.file_name || "batch"}
        </span>
        <span className="text-xs text-slate-400">
          {job.total} rows: {job.inserted} added, {job.updated} updated, {job.failed} refused
        </span>
        <span className="ml-auto font-mono text-[10px] text-slate-500">{job.brt_id}</span>
        {job.errors.length > 0 && (
          <button type="button" onClick={() => setOpen(!open)} className="text-xs text-sky-400">
            {open ? "Hide" : "Why refused"}
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">
          {job.errors.map((e) => (
            <p key={e.row} className="text-xs text-slate-300">
              <span className="text-slate-500">Row {e.row}</span> {e.brt_id && <span className="font-mono text-slate-400">{e.brt_id}</span>} - {e.errors.join("; ")}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export default function AdminBimSection() {
  const [entities, setEntities] = useState<BimEntity[]>([]);
  const [entity, setEntity] = useState("prospect");
  const [interfaces, setInterfaces] = useState<BimInterface[]>([]);
  const [jobs, setJobs] = useState<BimJob[]>([]);
  const [count, setCount] = useState(10);
  const [ids, setIds] = useState<string[]>([]);
  const [file, setFile] = useState<{ name: string; content: string; format: "csv" | "json" } | null>(null);
  const [interfaceId, setInterfaceId] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [pageRules, setPageRules] = useState(true);
  const [result, setResult] = useState<BimJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [lookup, setLookup] = useState("");
  const [found, setFound] = useState<Record<string, unknown> | null>(null);
  const [draft, setDraft] = useState<{ id?: number; code: string; name: string; entity: string; mode: "upsert" | "insert"; mapping: string; description: string } | null>(null);

  const reload = useCallback(() => {
    bimJobs().then(setJobs).catch(() => null);
    bimInterfaces().then(setInterfaces).catch(() => null);
  }, []);

  useEffect(() => {
    bimEntities().then(setEntities).catch((e) => setError(clean(e)));
    reload();
  }, [reload]);

  const spec = entities.find((e) => e.key === entity);
  const chosenInterface = interfaces.find((i) => String(i.id) === interfaceId);

  const run = async (dry: boolean) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const job = await bimLoad({
        entity: chosenInterface ? undefined : entity, interface_id: chosenInterface?.id ?? null, content: file.content, format: file.format,
        file_name: file.name, dry_run: dry, apply_page_rules: pageRules,
      });
      setResult(job);
      reload();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  const saveInterface = async () => {
    if (!draft) return;
    setError("");
    try {
      const mapping = Object.fromEntries(
        draft.mapping
          .split("\n")
          .map((line) => line.split(/=>|->|=|:/).map((s) => s.trim()))
          .filter((p) => p.length === 2 && p[0] && p[1])
      );
      await saveBimInterface({ code: draft.code, name: draft.name, entity: draft.entity, mode: draft.mode, mapping, description: draft.description, is_active: true }, draft.id);
      setDraft(null);
      reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">BIM - BRT Integration Manager</h2>
        <p className="text-sm text-slate-400">
          The only way data comes into BRT 360 from outside - files in batches, or record by record over the EAI API. Every record must carry a <b>BRT_ID</b> issued by BRT
          360 for that kind of record: reserve them here first. Rows without one, with one BRT 360 never issued, or of the wrong kind are refused.
        </p>
      </div>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="space-y-3 rounded-xl border border-slate-800 p-4">
          <h3 className="text-sm font-semibold text-slate-200">1 · Get BRT_IDs</h3>
          <div className="flex flex-wrap gap-2">
            <select value={entity} onChange={(e) => setEntity(e.target.value)} className={`${INPUT_CLS} w-auto`}>
              {entities.map((e) => (
                <option key={e.key} value={e.key}>
                  {e.label} ({e.prefix}-…)
                </option>
              ))}
            </select>
            <input type="number" min={1} max={1000} value={count} onChange={(e) => setCount(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))} className={`${INPUT_CLS} w-24`} />
            <button
              type="button"
              disabled={busy}
              onClick={() => reserveBrtIds(entity, count).then((r) => setIds(r.brt_ids)).catch((e) => setError(clean(e)))}
              className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200"
            >
              Reserve
            </button>
            <button
              type="button"
              onClick={() => bimTemplate(entity, count).then((csv) => download(`${entity}_template.csv`, csv)).catch((e) => setError(clean(e)))}
              className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white"
            >
              ⬇ Template with {count} BRT_IDs
            </button>
          </div>
          {ids.length > 0 && (
            <div className="space-y-1">
              <textarea readOnly rows={Math.min(6, ids.length)} value={ids.join("\n")} className={`${INPUT_CLS} font-mono text-xs`} />
              <button type="button" onClick={() => navigator.clipboard?.writeText(ids.join("\n"))} className="text-xs text-sky-400">
                Copy
              </button>
            </div>
          )}
          {spec && (
            <div className="text-xs text-slate-400">
              <p className="mb-1 font-semibold text-slate-300">Columns for {spec.label.toLowerCase()}</p>
              <div className="flex flex-wrap gap-1">
                {spec.columns.map((c) => (
                  <span key={c.name} title={c.help} className={`rounded px-1.5 py-0.5 font-mono ${c.required ? "bg-sky-500/15 text-sky-300" : "bg-slate-800 text-slate-300"}`}>
                    {c.name}
                    {c.required ? "*" : ""}
                  </span>
                ))}
                {spec.custom_fields && <span className="text-slate-500">+ any field of its Page Designer pages</span>}
              </div>
            </div>
          )}
        </section>

        <section className="space-y-3 rounded-xl border border-slate-800 p-4">
          <h3 className="text-sm font-semibold text-slate-200">2 · Load a file</h3>
          <div className="flex flex-wrap gap-2">
            <select value={interfaceId} onChange={(e) => setInterfaceId(e.target.value)} className={`${INPUT_CLS} w-auto`}>
              <option value="">Direct - {spec?.label ?? entity} columns</option>
              {interfaces.filter((i) => i.is_active).map((i) => (
                <option key={i.id} value={i.id}>
                  Interface {i.code} ({i.entity}, {i.mode})
                </option>
              ))}
            </select>
            <label className="cursor-pointer rounded-lg border border-dashed border-slate-600 px-3 py-1.5 text-xs text-slate-300 hover:border-sky-500">
              {file ? `📄 ${file.name}` : "Choose CSV / JSON…"}
              <input
                type="file"
                accept=".csv,.json,text/csv,application/json"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  f.text().then((content) => setFile({ name: f.name, content, format: f.name.toLowerCase().endsWith(".json") ? "json" : "csv" }));
                  setResult(null);
                }}
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-slate-300">
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> Dry run first (check only)
            </label>
            <label className="flex items-center gap-1.5">
              <input type="checkbox" checked={pageRules} onChange={(e) => setPageRules(e.target.checked)} /> Apply page mandatory fields & rules
            </label>
          </div>
          <div className="flex gap-2">
            <button type="button" disabled={!file || busy} onClick={() => run(dryRun)} className="rounded-lg bg-sky-600 px-4 py-1.5 text-sm font-semibold text-white disabled:opacity-40">
              {busy ? "Loading…" : dryRun ? "Check file" : "Load"}
            </button>
            {result?.dry_run && result.failed === 0 && (
              <button type="button" disabled={busy} onClick={() => run(false)} className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white">
                All good - load it now
              </button>
            )}
          </div>
          {result && <JobView job={result} />}
        </section>
      </div>

      <section className="space-y-3 rounded-xl border border-slate-800 p-4">
        <div className="flex items-center gap-2">
          <h3 className="flex-1 text-sm font-semibold text-slate-200">EAI interfaces</h3>
          <button
            type="button"
            onClick={() => setDraft({ code: "", name: "", entity, mode: "upsert", mapping: "", description: "" })}
            className="rounded-lg border border-sky-600 px-2.5 py-1 text-xs font-semibold text-sky-300"
          >
            + New interface
          </button>
        </div>
        <p className="text-xs text-slate-400">
          A source system posts records to <code className="rounded bg-slate-800 px-1">POST /api/bim/eai/&lt;CODE&gt;</code> (one record, a list, or{" "}
          <code className="rounded bg-slate-800 px-1">{'{"records": [...]}'}</code>) with an administrator's sign-in token. The mapping turns its column names into BRT 360
          fields; <b>insert</b> refuses BRT_IDs already loaded, <b>upsert</b> updates them. Read any record back with{" "}
          <code className="rounded bg-slate-800 px-1">GET /api/bim/records/&lt;BRT_ID&gt;</code>.
        </p>
        {interfaces.map((i) => (
          <div key={i.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
            <span className="font-mono text-sky-300">{i.code}</span>
            <span className="text-slate-200">{i.name}</span>
            <span className="text-xs text-slate-400">
              {i.entity} · {i.mode} · {Object.keys(i.mapping).length} mapped columns
            </span>
            <button
              type="button"
              onClick={() =>
                setDraft({ id: i.id, code: i.code, name: i.name, entity: i.entity, mode: i.mode, description: i.description, mapping: Object.entries(i.mapping).map(([k, v]) => `${k} => ${v}`).join("\n") })
              }
              className="ml-auto text-xs text-sky-400"
            >
              Edit
            </button>
          </div>
        ))}
        {draft && (
          <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
            <input value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} placeholder="Code, e.g. SAP-CUSTOMERS" className={INPUT_CLS} />
            <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Name" className={INPUT_CLS} />
            <select value={draft.entity} onChange={(e) => setDraft({ ...draft, entity: e.target.value })} className={INPUT_CLS}>
              {entities.map((e) => (
                <option key={e.key} value={e.key}>
                  {e.label}
                </option>
              ))}
            </select>
            <select value={draft.mode} onChange={(e) => setDraft({ ...draft, mode: e.target.value as "upsert" | "insert" })} className={INPUT_CLS}>
              <option value="upsert">Upsert - a known BRT_ID updates the record</option>
              <option value="insert">Insert only - a known BRT_ID is refused</option>
            </select>
            <textarea
              rows={4}
              value={draft.mapping}
              onChange={(e) => setDraft({ ...draft, mapping: e.target.value })}
              placeholder={"Mapping, one per line: source column => BRT 360 field\nCustomerName => name\nGST => tax_id\nBRT => brt_id"}
              className={`${INPUT_CLS} font-mono text-xs sm:col-span-2`}
            />
            <div className="flex gap-2 sm:col-span-2">
              <button type="button" disabled={!draft.code.trim() || !draft.name.trim()} onClick={saveInterface} className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
                Save interface
              </button>
              <button type="button" onClick={() => setDraft(null)} className="text-xs text-slate-400">
                Cancel
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="space-y-2 rounded-xl border border-slate-800 p-4">
        <h3 className="text-sm font-semibold text-slate-200">Find a record by BRT_ID</h3>
        <div className="flex gap-2">
          <input value={lookup} onChange={(e) => setLookup(e.target.value)} placeholder="e.g. PTY-7K3MX9QP-H" className={`${INPUT_CLS} font-mono`} />
          <button
            type="button"
            disabled={!lookup.trim()}
            onClick={() => bimRecord(lookup.trim()).then(setFound).catch(() => (setFound(null), setError(`No record with BRT_ID ${lookup.trim()}`)))}
            className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200"
          >
            Find
          </button>
        </div>
        {found && (
          <div className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
            {Object.entries(found)
              .filter(([, v]) => v !== null && v !== "")
              .map(([k, v]) => (
                <p key={k}>
                  <span className="text-slate-500">{k}</span> <span className="text-slate-200">{String(v)}</span>
                </p>
              ))}
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-200">Load history</h3>
        {jobs.length === 0 && <p className="text-xs text-slate-500">Nothing loaded yet.</p>}
        {jobs.map((j) => (
          <JobView key={j.id} job={j} />
        ))}
      </section>
    </div>
  );
}
