import { useCallback, useEffect, useState } from "react";
import {
  bimEntities,
  bimInterfaces,
  bimJob,
  bimJobs,
  bimLoad,
  bimRecord,
  bimReprocess,
  bimTemplate,
  downloadFile,
  hubApi,
  reserveBrtIds,
  saveBimInterface,
  type BimEntity,
  type BimInterface,
  type BimJob,
  type HubOperation,
} from "../api/client";
import { INPUT_CLS } from "../components/intent/intentUi";
import { mappedCount, mappingText, parseMapping } from "./integrationMapping";

const STATUS_CLS: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-300",
  completed_with_errors: "bg-amber-500/15 text-amber-300",
  failed: "bg-red-500/15 text-red-300",
  running: "bg-sky-500/15 text-sky-300",
  queued: "bg-sky-500/15 text-sky-300",
  interrupted: "bg-red-500/15 text-red-300",
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

function JobView({ job, onChanged, onError }: { job: BimJob; onChanged?: () => void; onError?: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const again = async () => {
    setBusy(true);
    try {
      await bimReprocess(job.id);
      onChanged?.();
    } catch (e) {
      onError?.(clean(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-lg border border-slate-800 px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS_CLS[job.status] ?? ""}`}>{job.status.replace(/_/g, " ")}</span>
        {job.dry_run && <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300">dry run - nothing kept</span>}
        <span className="text-slate-200">
          {job.entity} · {job.channel === "api" ? "API" : job.channel === "pull" ? "scheduled pull" : job.file_name || "batch"}
          {job.parent_job_id ? " · reprocessed" : ""}
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
        {job.can_reprocess && (
          <>
            <button type="button" onClick={() => downloadFile(`/api/bim/jobs/${job.id}/errors.csv`, `bim-job-${job.id}-errors.csv`).catch((e) => onError?.(clean(e)))} className="text-xs text-sky-400">
              Refused rows ⬇
            </button>
            {onChanged && (
              <button type="button" disabled={busy} title="Load the refused rows again - after fixing the mapping or what they depend on" onClick={again} className="text-xs text-sky-400 disabled:opacity-40">
                {busy ? "Loading…" : "Load refused rows again"}
              </button>
            )}
          </>
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

interface InterfaceDraft {
  id?: number;
  code: string;
  name: string;
  entity: string;
  mode: "upsert" | "insert";
  mapping: string;
  description: string;
  is_active: boolean;
  ignore_unmapped: boolean;
  source_system: string;
  key_field: string;
  ack: string;
  pull: string;
  every: string;
  records_path: string;
}

const BLANK_INTERFACE: InterfaceDraft = { code: "", name: "", entity: "prospect", mode: "upsert", mapping: "", description: "", is_active: true, ignore_unmapped: false, source_system: "", key_field: "", ack: "", pull: "", every: "", records_path: "" };

export default function AdminBimSection() {
  const [entities, setEntities] = useState<BimEntity[]>([]);
  const [entity, setEntity] = useState("prospect");
  const [interfaces, setInterfaces] = useState<BimInterface[]>([]);
  const [jobs, setJobs] = useState<BimJob[]>([]);
  const [count, setCount] = useState(10);
  const [ids, setIds] = useState<string[]>([]);
  const [file, setFile] = useState<{ name: string; content: string; format: "csv" | "json" | "xlsx" } | null>(null);
  const [interfaceId, setInterfaceId] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [pageRules, setPageRules] = useState(true);
  const [result, setResult] = useState<BimJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [lookup, setLookup] = useState("");
  const [found, setFound] = useState<Record<string, unknown> | null>(null);
  const [draft, setDraft] = useState<InterfaceDraft | null>(null);
  const [operations, setOperations] = useState<HubOperation[]>([]);
  const [sample, setSample] = useState("");
  const [preview, setPreview] = useState("");

  const reload = useCallback(() => {
    bimJobs().then(setJobs).catch(() => null);
    bimInterfaces().then(setInterfaces).catch(() => null);
    hubApi<HubOperation[]>("/operations").then(setOperations).catch(() => null);
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
      let job = await bimLoad({
        entity: chosenInterface ? undefined : entity, interface_id: chosenInterface?.id ?? null, content: file.content, format: file.format,
        file_name: file.name, dry_run: dry, apply_page_rules: pageRules,
      });
      setResult(job);
      // a large load runs in the background: follow it until it's done
      while (job.status === "queued" || job.status === "running") {
        await new Promise((r) => setTimeout(r, 2000));
        job = await bimJob(job.id);
        setResult(job);
      }
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
      await saveBimInterface({ code: draft.code, name: draft.name, entity: draft.entity, mode: draft.mode, mapping: parseMapping(draft.mapping), description: draft.description, is_active: draft.is_active,
        ignore_unmapped: draft.ignore_unmapped, source_system: draft.source_system, key_field: draft.key_field, ack_operation_id: draft.ack ? Number(draft.ack) : null,
        pull_operation_id: draft.pull ? Number(draft.pull) : null, pull_every_minutes: draft.pull ? Number(draft.every) || 0 : 0, pull_records_path: draft.records_path }, draft.id);
      setDraft(null);
      setPreview("");
      reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  const runPreview = async () => {
    if (!draft) return;
    try {
      const out = await hubApi<{ ok: boolean; result?: unknown; error?: string }>("/mapping/preview", { method: "POST", body: JSON.stringify({ mapping: parseMapping(draft.mapping), sample: JSON.parse(sample || "{}"), keep_unmapped: !draft.ignore_unmapped }) });
      setPreview(out.ok ? JSON.stringify(out.result, null, 2) : `Refused: ${out.error}`);
    } catch (e) {
      setPreview(`Can't preview: ${clean(e)}`);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">BIM - BRT Integration Manager</h2>
        <p className="text-sm text-slate-400">
          Bulk data coming into BRT 360 - files in batches, or from other systems over the EAI interfaces below. Every record carries a <b>BRT_ID</b> issued by BRT 360: reserve
          them here for a file, or give an interface a <b>source key</b> and BRT 360 issues and remembers them for you. Rows with an ID BRT 360 never issued, or of the wrong kind, are refused.
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
              onClick={() => downloadFile(`/api/bim/template/${encodeURIComponent(entity)}?rows=${count}&format=xlsx`, `${entity}_template.xlsx`).catch((e) => setError(clean(e)))}
              className="rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white"
            >
              ⬇ Excel template with {count} BRT_IDs
            </button>
            <button
              type="button"
              onClick={() => bimTemplate(entity, count).then((csv) => download(`${entity}_template.csv`, csv)).catch((e) => setError(clean(e)))}
              className="rounded-lg border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-200"
            >
              CSV
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
              {file ? `📄 ${file.name}` : "Choose Excel / CSV / JSON…"}
              <input
                type="file"
                accept=".xlsx,.csv,.json,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  if (f.name.toLowerCase().endsWith(".xlsx")) {
                    // the Excel template, filled in - sent base64-encoded, its first sheet is read like a CSV
                    f.arrayBuffer().then((buf) => {
                      let binary = "";
                      new Uint8Array(buf).forEach((b) => { binary += String.fromCharCode(b); });
                      setFile({ name: f.name, content: btoa(binary), format: "xlsx" });
                    });
                  } else {
                    f.text().then((content) => setFile({ name: f.name, content, format: f.name.toLowerCase().endsWith(".json") ? "json" : "csv" }));
                  }
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
            onClick={() => setDraft({ ...BLANK_INTERFACE, entity })}
            className="rounded-lg border border-sky-600 px-2.5 py-1 text-xs font-semibold text-sky-300"
          >
            + New interface
          </button>
        </div>
        <p className="text-xs text-slate-400">
          Each active interface publishes itself to other systems, with a key of their own (Admin → <b>Integration Hub</b> → Inbound - no administrator's sign-in):{" "}
          <code className="rounded bg-slate-800 px-1">POST /api/gateway/services/&lt;CODE&gt;</code> over JSON, XML or CSV, or SOAP from the WSDL. Add{" "}
          <code className="rounded bg-slate-800 px-1">Prefer: respond-async</code> for large loads and <code className="rounded bg-slate-800 px-1">?dry_run=true</code> to check without keeping.
          They read changes back from <code className="rounded bg-slate-800 px-1">GET /api/gateway/eai/&lt;CODE&gt;/changes</code>.
        </p>
        {interfaces.map((i) => (
          <div key={i.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
            <span className="font-mono text-sky-300">{i.code}</span>
            <span className="text-slate-200">{i.name}</span>
            <span className="text-xs text-slate-400">
              {i.entity} · {i.mode} · {mappedCount(i.mapping)} mapped{i.key_field ? ` · key ${i.key_field} (${i.source_system || i.code})` : " · needs BRT_IDs"}
              {i.pull_operation_id && i.pull_every_minutes ? ` · pulls every ${i.pull_every_minutes} min` : ""}{i.pull_last_at ? `, last ${new Date(`${i.pull_last_at}Z`).toLocaleString()}` : ""}
              {i.ack_operation_id ? " · acknowledges" : ""}{i.is_active ? "" : " · switched off"}
            </span>
            <button
              type="button"
              onClick={() => {
                setPreview("");
                setDraft({ id: i.id, code: i.code, name: i.name, entity: i.entity, mode: i.mode, description: i.description, mapping: mappingText(i.mapping), is_active: i.is_active, ignore_unmapped: i.ignore_unmapped,
                  source_system: i.source_system, key_field: i.key_field, ack: i.ack_operation_id ? String(i.ack_operation_id) : "", pull: i.pull_operation_id ? String(i.pull_operation_id) : "", every: i.pull_every_minutes ? String(i.pull_every_minutes) : "", records_path: i.pull_records_path });
              }}
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
            <label className="block text-xs text-slate-400">
              Source key - that system's own id for a record
              <input value={draft.key_field} onChange={(e) => setDraft({ ...draft, key_field: e.target.value })} placeholder="e.g. CustomerNo (blank: rows bring BRT_IDs)" className={INPUT_CLS} />
              <span className="mt-0.5 block text-[11px] text-slate-500">BRT 360 issues the BRT_ID the first time it sees an id, and that id updates the same record ever after. References work too: party_external_id.</span>
            </label>
            <label className="block text-xs text-slate-400">
              Source system
              <input value={draft.source_system} onChange={(e) => setDraft({ ...draft, source_system: e.target.value })} placeholder={draft.code || "SAP"} className={INPUT_CLS} />
              <span className="mt-0.5 block text-[11px] text-slate-500">Whose ids they are. Interfaces of one system share it, so a lead can refer to a customer loaded by another. Blank: this interface's code.</span>
            </label>
            <label className="block text-xs text-slate-400 sm:col-span-2">
              Mapping - one per line
              <textarea
                rows={5}
                value={draft.mapping}
                onChange={(e) => setDraft({ ...draft, mapping: e.target.value })}
                placeholder={"CustomerName => name\ncustomer.mail => email | lower\ntype => segment | map:C=B2C,E=B2B | default:B2B\nGST => tax_id | upper | required\n= source | value:SAP"}
                className={`${INPUT_CLS} font-mono text-xs`}
              />
              <span className="mt-0.5 block text-[11px] text-slate-500">Nested fields with dots. Transforms: trim, upper, lower, title, digits, number, integer, boolean, date. Also required, default:, map:, and = field | value: for a fixed value.</span>
            </label>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 sm:col-span-2">
              <input type="checkbox" checked={draft.ignore_unmapped} onChange={(e) => setDraft({ ...draft, ignore_unmapped: e.target.checked })} /> Leave out fields the mapping doesn't name (otherwise an unknown one refuses the row)
            </label>
            <details className="text-xs text-slate-400 sm:col-span-2">
              <summary className="cursor-pointer">Try the mapping on a sample record</summary>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <textarea rows={4} value={sample} onChange={(e) => setSample(e.target.value)} placeholder={'{"CustomerName": "ACME", "customer": {"mail": "A@B.CO"}}'} className={`${INPUT_CLS} font-mono text-xs`} />
                <pre className="max-h-40 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-2 text-[11px] whitespace-pre-wrap text-slate-300">{preview || "The result shows here"}</pre>
              </div>
              <button type="button" onClick={runPreview} className="mt-1 rounded-lg border border-slate-600 px-2.5 py-1 text-xs font-semibold text-slate-200">Preview</button>
            </details>
            <label className="block text-xs text-slate-400">
              Fetch the records on a schedule, with
              <select value={draft.pull} onChange={(e) => setDraft({ ...draft, pull: e.target.value })} className={INPUT_CLS}>
                <option value="">Nothing - they send them</option>
                {operations.map((o) => <option key={o.id} value={o.id}>{o.code}</option>)}
              </select>
              <span className="mt-0.5 block text-[11px] text-slate-500">An operation from the Integration Hub. {"{{cursor}}"} in its address is when the last good pull started.</span>
            </label>
            {draft.pull ? (
              <div className="grid grid-cols-2 gap-2">
                <label className="block text-xs text-slate-400">Every (minutes)<input type="number" min={1} value={draft.every} onChange={(e) => setDraft({ ...draft, every: e.target.value })} className={INPUT_CLS} /></label>
                <label className="block text-xs text-slate-400">Records are at<input value={draft.records_path} onChange={(e) => setDraft({ ...draft, records_path: e.target.value })} placeholder="data.items" className={INPUT_CLS} /></label>
              </div>
            ) : <div />}
            <label className="block text-xs text-slate-400">
              After each load, tell them how it went
              <select value={draft.ack} onChange={(e) => setDraft({ ...draft, ack: e.target.value })} className={INPUT_CLS}>
                <option value="">Don't - they read the answer</option>
                {operations.map((o) => <option key={o.id} value={o.id}>{o.code}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1.5 self-end text-xs text-slate-300">
              <input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /> Active - published and pulling
            </label>
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
          <JobView key={j.id} job={j} onChanged={reload} onError={setError} />
        ))}
      </section>
    </div>
  );
}
