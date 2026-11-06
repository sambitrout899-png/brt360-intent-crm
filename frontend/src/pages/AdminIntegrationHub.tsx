import { useCallback, useEffect, useState, type ReactNode } from "react";
import { downloadFile, hubApi, type HubClient, type HubConnection, type HubMessage, type HubOperation, type HubOverview, type HubPublished, type HubService, type HubSubscription, type HubTargets } from "../api/client";
import { INPUT_CLS } from "../components/intent/intentUi";
import { mappingText, parseMapping, type Mapping } from "./integrationMapping";

const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");
const BTN = "rounded-lg border border-slate-600 px-2.5 py-1 text-xs font-semibold text-slate-200 hover:border-sky-500 disabled:opacity-40";
const PRIMARY = "rounded-lg bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40";
const LINK = "text-xs text-sky-400 hover:underline";
const CARD = "space-y-3 rounded-xl border border-slate-800 p-4";

const STATUS: Record<string, string> = {
  queued: "bg-sky-500/15 text-sky-300", sending: "bg-sky-500/15 text-sky-300", processing: "bg-sky-500/15 text-sky-300",
  awaiting_reply: "bg-violet-500/15 text-violet-300",
  delivered: "bg-emerald-500/15 text-emerald-300", replied: "bg-emerald-500/15 text-emerald-300", succeeded: "bg-emerald-500/15 text-emerald-300",
  failed: "bg-red-500/15 text-red-300", dead: "bg-red-500/15 text-red-300", timed_out: "bg-amber-500/15 text-amber-300", cancelled: "bg-slate-700 text-slate-300",
};

function Badge({ status }: { status: string }) {
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${STATUS[status] ?? "bg-slate-800 text-slate-300"}`}>{status.replace(/_/g, " ")}</span>;
}

function Field({ label, hint, children, wide }: { label: string; hint?: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`block text-xs text-slate-400 ${wide ? "sm:col-span-2" : ""}`}>
      {label}
      {children}
      {hint && <span className="mt-0.5 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

function Code({ children }: { children: ReactNode }) {
  return <code className="rounded bg-slate-800 px-1 text-[11px] text-slate-200">{children}</code>;
}

function Pre({ children }: { children: string }) {
  return <pre className="max-h-64 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-2 text-[11px] whitespace-pre-wrap break-all text-slate-300">{children}</pre>;
}

function pretty(text: string | null | undefined): string {
  if (!text) return "";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function parseJson(text: string, what: string): unknown {
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`${what} isn't valid JSON`);
  }
}

const when = (value: string | null | undefined) => (value ? new Date(`${value}${/[zZ]|[+-]\d\d:\d\d$/.test(value) ? "" : "Z"}`).toLocaleString() : "");

// ---------------------------------------------------------------- overview

function Stat({ label, value, tone, onClick }: { label: string; value: ReactNode; tone?: string; onClick?: () => void }) {
  return (
    <button type="button" disabled={!onClick} onClick={onClick} className="rounded-xl border border-slate-800 p-3 text-left enabled:hover:border-sky-500">
      <p className="text-[11px] text-slate-400">{label}</p>
      <p className={`mt-1 text-xl font-semibold ${tone ?? "text-slate-100"}`}>{value}</p>
    </button>
  );
}

function Overview({ goMessages }: { goMessages: (status: string) => void }) {
  const [data, setData] = useState<HubOverview | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const load = () => hubApi<HubOverview>("/overview").then(setData).catch((e) => setError(clean(e)));
    load();
    const timer = window.setInterval(load, 15000);
    return () => window.clearInterval(timer);
  }, []);
  const origin = window.location.origin;
  const sum = (o: Record<string, number> | undefined) => Object.values(o ?? {}).reduce((a, b) => a + b, 0);
  return (
    <div className="space-y-4">
      {error && <p className="text-sm text-red-300">{error}</p>}
      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
            <Stat label="Dead letters" value={data.dead_letters} tone={data.dead_letters ? "text-red-300" : "text-emerald-300"} onClick={() => goMessages("dead")} />
            <Stat label="In the queue" value={data.in_queue} onClick={() => goMessages("queued")} />
            <Stat label="Waiting for a reply" value={data.awaiting_reply} onClick={() => goMessages("awaiting_reply")} />
            <Stat label="Inbound, 24 h" value={sum(data.last_24h.in)} />
            <Stat label="Outbound, 24 h" value={sum(data.last_24h.out)} />
            <Stat label="Average call" value={data.avg_latency_ms != null ? `${data.avg_latency_ms} ms` : "-"} />
          </div>
          {data.tripped_connections.length > 0 && (
            <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-200">
              Being left alone after repeated failures: {data.tripped_connections.map((c) => `${c.name} (until ${when(c.until)})`).join(", ")}. Queued calls wait and use up no retries.
            </p>
          )}
          <p className="text-xs text-slate-400">
            {data.totals.clients} client(s) may call in · {data.totals.services} service(s) published · {data.totals.connections} connection(s) ·{" "}
            {data.totals.operations} operation(s) · {data.totals.subscriptions} webhook(s)
          </p>
        </>
      )}
      <section className={CARD}>
        <h3 className="text-sm font-semibold text-slate-200">How other systems call BRT 360</h3>
        <Pre>{`# 1. Inbound → create a client: it gets its own key, shown once.
# 2. Call a published service - an intent, a flow or a BIM interface:
curl -X POST ${origin}/api/gateway/services/NEW_PROSPECT \\
  -H "X-API-Key: brt_..." -H "Content-Type: application/json" \\
  -H "Idempotency-Key: your-unique-id" \\
  -d '{"name": "Acme Fibre"}'

# Asynchronous: answers 202 at once, then read the outcome (or set a callback)
curl ... -H "Prefer: respond-async"
curl ${origin}/api/gateway/messages/<correlation_id> -H "X-API-Key: brt_..."

# SOAP: point your toolkit at the WSDL (HTTP Basic: key id, key)
${origin}/api/gateway/soap?wsdl

# OAuth2 client credentials, if your side prefers tokens
curl -X POST ${origin}/api/gateway/oauth/token \\
  -d "grant_type=client_credentials&client_id=<key id>&client_secret=<key>"

# OpenAPI, for code generators and Postman
${origin}/api/gateway/openapi.json`}</Pre>
      </section>
      <section className={CARD}>
        <h3 className="text-sm font-semibold text-slate-200">How BRT 360 calls other systems</h3>
        <p className="text-xs text-slate-400">
          Outbound → add a connection and its operations. Use them from Flow Studio with <b>Call external API</b> or <b>Call API and wait for reply</b>, from an event under Webhooks, or as a BIM interface's scheduled pull or acknowledgement.
        </p>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- inbound

function KeyReveal({ value, onClose }: { value: { name: string; key: string; key_id: string }; onClose: () => void }) {
  return (
    <div role="alert" className="space-y-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-100">
      <p className="font-semibold">The key for {value.name} - copy it now. It is not stored and can't be shown again.</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 rounded bg-slate-950 px-2 py-1 font-mono text-xs break-all text-slate-100">{value.key}</code>
        <button type="button" className={BTN} onClick={() => navigator.clipboard?.writeText(value.key)}>Copy</button>
      </div>
      <p className="text-xs">Key id (OAuth2 client_id, Basic user name): <code className="font-mono">{value.key_id}</code></p>
      <button type="button" className={LINK} onClick={onClose}>I have saved it</button>
    </div>
  );
}

const blankClient = { code: "", name: "", run_as_user_id: 0, everything: true, allowed: [] as string[], ip_allowlist: "", rate_per_minute: 600, expires_at: "", is_active: true };

function Inbound({ targets, operations }: { targets: HubTargets | null; operations: HubOperation[] }) {
  const [clients, setClients] = useState<HubClient[]>([]);
  const [published, setPublished] = useState<HubPublished[]>([]);
  const [services, setServices] = useState<HubService[]>([]);
  const [reveal, setReveal] = useState<{ name: string; key: string; key_id: string } | null>(null);
  const [draft, setDraft] = useState<(typeof blankClient & { id?: number }) | null>(null);
  const [svc, setSvc] = useState<{ id?: number; code: string; name: string; description: string; target_type: string; target: string; mode: string; fields: string; input: string; output: string; callback: string; is_active: boolean } | null>(null);
  const [tryOut, setTryOut] = useState<{ code: string; payload: string; answer?: string } | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(() => {
    hubApi<HubClient[]>("/clients").then(setClients).catch((e) => setError(clean(e)));
    hubApi<{ services: HubService[]; published: HubPublished[] }>("/services").then((r) => { setServices(r.services); setPublished(r.published); }).catch((e) => setError(clean(e)));
  }, []);
  useEffect(reload, [reload]);

  const saveClient = async () => {
    if (!draft) return;
    setError("");
    try {
      const body = { code: draft.code, name: draft.name, run_as_user_id: Number(draft.run_as_user_id), allowed_services: draft.everything ? ["*"] : draft.allowed, ip_allowlist: draft.ip_allowlist,
        rate_per_minute: Number(draft.rate_per_minute), expires_at: draft.expires_at || null, is_active: draft.is_active };
      const saved = await hubApi<HubClient & { key?: string }>(draft.id ? `/clients/${draft.id}` : "/clients", { method: draft.id ? "PUT" : "POST", body: JSON.stringify(body) });
      if (saved.key) setReveal({ name: saved.name, key: saved.key, key_id: saved.key_id });
      setDraft(null);
      reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  const rotate = async (client: HubClient) => {
    setError("");
    try {
      const saved = await hubApi<HubClient & { key: string }>(`/clients/${client.id}/rotate`, { method: "POST" });
      setReveal({ name: saved.name, key: saved.key, key_id: saved.key_id });
      reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  const saveService = async () => {
    if (!svc) return;
    setError("");
    try {
      const body = { code: svc.code, name: svc.name, description: svc.description, target_type: svc.target_type, target: svc.target, mode: svc.mode, is_active: svc.is_active,
        fields: svc.fields.trim() ? parseJson(svc.fields, "Published fields") : [], input_mapping: svc.input.trim() ? parseMapping(svc.input) : null,
        output_mapping: svc.output.trim() ? parseMapping(svc.output) : null, callback_operation_id: svc.callback ? Number(svc.callback) : null };
      await hubApi(svc.id ? `/services/${svc.id}` : "/services", { method: svc.id ? "PUT" : "POST", body: JSON.stringify(body) });
      setSvc(null);
      reload();
    } catch (e) {
      setError(clean(e));
    }
  };

  const runTry = async () => {
    if (!tryOut) return;
    try {
      const out = await hubApi<{ http_status: number; answer: unknown }>("/services-try", { method: "POST", body: JSON.stringify({ code: tryOut.code, payload: parseJson(tryOut.payload, "The sample") }) });
      setTryOut({ ...tryOut, answer: `HTTP ${out.http_status}\n${JSON.stringify(out.answer, null, 2)}` });
    } catch (e) {
      setTryOut({ ...tryOut, answer: clean(e) });
    }
  };

  return (
    <div className="space-y-5">
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
      {reveal && <KeyReveal value={reveal} onClose={() => setReveal(null)} />}

      <section className={CARD}>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="flex-1 text-sm font-semibold text-slate-200">Clients - the systems allowed in</h3>
          <button type="button" className={BTN} onClick={() => setDraft({ ...blankClient })}>+ New client</button>
        </div>
        <p className="text-xs text-slate-400">Each has its own key and runs as a user you choose: that user's position, data scope and permissions apply, and the audit trail shows them. Give it the fewest rights that will do.</p>
        {clients.length === 0 && <p className="text-xs text-slate-500">No client yet.</p>}
        {clients.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
            <span className="font-mono text-sky-300">{c.code}</span>
            <span className="text-slate-200">{c.name}</span>
            {!c.is_active && <Badge status="cancelled" />}
            <span className="text-xs text-slate-400">
              {c.allowed_services.includes("*") ? "all services" : c.allowed_services.join(", ") || "no services"} · {c.rate_per_minute ? `${c.rate_per_minute}/min` : "no limit"}{c.ip_allowlist ? ` · from ${c.ip_allowlist}` : ""}
              {c.last_used_at ? ` · last used ${when(c.last_used_at)}` : " · never used"}{c.rotating_until ? ` · old key works until ${when(c.rotating_until)}` : ""}
            </span>
            <span className="ml-auto flex gap-2">
              <button type="button" className={LINK} onClick={() => setDraft({ id: c.id, code: c.code, name: c.name, run_as_user_id: c.run_as_user_id, everything: c.allowed_services.includes("*"), allowed: c.allowed_services.filter((s) => s !== "*"),
                ip_allowlist: c.ip_allowlist, rate_per_minute: c.rate_per_minute, expires_at: c.expires_at ?? "", is_active: c.is_active })}>Edit</button>
              <button type="button" className={LINK} onClick={() => rotate(c)}>New key</button>
              {c.rotating_until && <button type="button" className={LINK} onClick={() => hubApi(`/clients/${c.id}/revoke-previous`, { method: "POST" }).then(reload)}>End old key now</button>}
            </span>
          </div>
        ))}
        {draft && (
          <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
            <Field label="Code"><input value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} placeholder="SAP" className={INPUT_CLS} /></Field>
            <Field label="Name"><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="SAP S/4HANA" className={INPUT_CLS} /></Field>
            <Field label="Runs as" hint="A dedicated user with only the roles this system needs is safest">
              <select value={draft.run_as_user_id} onChange={(e) => setDraft({ ...draft, run_as_user_id: Number(e.target.value) })} className={INPUT_CLS}>
                <option value={0}>Choose a user…</option>
                {targets?.users.map((u) => <option key={u.id} value={u.id}>{u.name} ({u.role})</option>)}
              </select>
            </Field>
            <Field label="Calls a minute" hint="0 = no limit"><input type="number" min={0} value={draft.rate_per_minute} onChange={(e) => setDraft({ ...draft, rate_per_minute: Number(e.target.value) })} className={INPUT_CLS} /></Field>
            <Field label="Only from these addresses" hint="Comma-separated addresses or networks; blank = anywhere"><input value={draft.ip_allowlist} onChange={(e) => setDraft({ ...draft, ip_allowlist: e.target.value })} placeholder="52.14.0.0/16, 8.8.4.4" className={INPUT_CLS} /></Field>
            <Field label="Key expires"><input type="date" value={(draft.expires_at || "").slice(0, 10)} onChange={(e) => setDraft({ ...draft, expires_at: e.target.value })} className={INPUT_CLS} /></Field>
            <div className="sm:col-span-2">
              <label className="flex items-center gap-1.5 text-xs text-slate-300"><input type="checkbox" checked={draft.everything} onChange={(e) => setDraft({ ...draft, everything: e.target.checked })} /> May call every service, including ones published later</label>
              {!draft.everything && (
                <div className="mt-1 flex flex-wrap gap-3">
                  {published.map((p) => (
                    <label key={p.code} className="flex items-center gap-1 text-xs text-slate-300">
                      <input type="checkbox" checked={draft.allowed.includes(p.code)} onChange={(e) => setDraft({ ...draft, allowed: e.target.checked ? [...draft.allowed, p.code] : draft.allowed.filter((a) => a !== p.code) })} />
                      <span className="font-mono">{p.code}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 sm:col-span-2"><input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /> Active</label>
            <div className="flex gap-2 sm:col-span-2">
              <button type="button" className={PRIMARY} disabled={!draft.code.trim() || !draft.name.trim() || !draft.run_as_user_id} onClick={saveClient}>{draft.id ? "Save" : "Create and show the key"}</button>
              <button type="button" className="text-xs text-slate-400" onClick={() => setDraft(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>

      <section className={CARD}>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="flex-1 text-sm font-semibold text-slate-200">Published services</h3>
          <button type="button" className={BTN} onClick={() => downloadFile("/api/integration/contracts/openapi.json", "brt360-integration-openapi.json").catch((e) => setError(clean(e)))}>⬇ OpenAPI</button>
          <button type="button" className={BTN} onClick={() => downloadFile("/api/integration/contracts/service.wsdl", "brt360-integration.wsdl").catch((e) => setError(clean(e)))}>⬇ WSDL</button>
          <button type="button" className={BTN} onClick={() => setSvc({ code: "", name: "", description: "", target_type: "action", target: "", mode: "either", fields: "", input: "", output: "", callback: "", is_active: true })}>+ Publish a service</button>
        </div>
        <p className="text-xs text-slate-400">An intent or flow runs through the same pipeline as a person - every check, approval and audit entry. Each active BIM interface publishes itself.</p>
        {published.length === 0 && <p className="text-xs text-slate-500">Nothing published yet.</p>}
        {published.map((p) => {
          const own = services.find((s) => s.code === p.code);
          return (
            <div key={p.code} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
              <span className="font-mono text-sky-300">{p.code}</span>
              <span className="text-slate-200">{p.name}</span>
              <span className="text-xs text-slate-400">{p.target_type === "bim" ? "BIM interface" : p.target} · {p.mode === "either" ? "sync or async" : p.mode} · {p.fields.length} field(s){p.from_interface ? " · from its interface" : ""}</span>
              <span className="ml-auto flex gap-2">
                <button type="button" className={LINK} onClick={() => setTryOut({ code: p.code, payload: JSON.stringify(Object.fromEntries(p.fields.slice(0, 6).map((f) => [f.name, ""])), null, 2) })}>Try it</button>
                {own && <button type="button" className={LINK} onClick={() => setSvc({ id: own.id, code: own.code, name: own.name, description: own.description, target_type: own.target_type, target: own.target, mode: own.mode, fields: own.fields.length ? JSON.stringify(own.fields, null, 2) : "",
                  input: mappingText(own.input_mapping as Mapping), output: mappingText(own.output_mapping as Mapping), callback: own.callback_operation_id ? String(own.callback_operation_id) : "", is_active: own.is_active })}>Edit</button>}
              </span>
            </div>
          );
        })}
        {tryOut && (
          <div className="space-y-2 rounded-lg border border-slate-700 p-3">
            <p className="text-xs text-slate-300">Dry run of <span className="font-mono">{tryOut.code}</span> as you - everything is checked, nothing is kept, nothing is sent out.</p>
            <textarea rows={6} value={tryOut.payload} onChange={(e) => setTryOut({ ...tryOut, payload: e.target.value })} className={`${INPUT_CLS} font-mono text-xs`} />
            <div className="flex gap-2"><button type="button" className={PRIMARY} onClick={runTry}>Run dry</button><button type="button" className="text-xs text-slate-400" onClick={() => setTryOut(null)}>Close</button></div>
            {tryOut.answer && <Pre>{tryOut.answer}</Pre>}
          </div>
        )}
        {svc && (
          <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
            <Field label="Code" hint="Its address: /api/gateway/services/CODE"><input value={svc.code} onChange={(e) => setSvc({ ...svc, code: e.target.value })} placeholder="NEW_PROSPECT" className={INPUT_CLS} /></Field>
            <Field label="Name"><input value={svc.name} onChange={(e) => setSvc({ ...svc, name: e.target.value })} className={INPUT_CLS} /></Field>
            <Field label="What it does">
              <select value={svc.target_type} onChange={(e) => setSvc({ ...svc, target_type: e.target.value, target: "" })} className={INPUT_CLS}>
                <option value="action">Runs an action (an intent or a flow)</option>
                <option value="bim">Loads through a BIM interface</option>
              </select>
            </Field>
            <Field label={svc.target_type === "bim" ? "Interface" : "Action"}>
              <select value={svc.target} onChange={(e) => setSvc({ ...svc, target: e.target.value })} className={INPUT_CLS}>
                <option value="">Choose…</option>
                {svc.target_type === "bim"
                  ? targets?.interfaces.map((i) => <option key={i.code} value={i.code}>{i.code} - {i.name}</option>)
                  : targets?.actions.map((a) => <option key={a.code} value={a.code}>{a.code} - {a.label}</option>)}
              </select>
            </Field>
            <Field label="How it answers">
              <select value={svc.mode} onChange={(e) => setSvc({ ...svc, mode: e.target.value })} className={INPUT_CLS}>
                <option value="either">The caller chooses (Prefer: respond-async)</option>
                <option value="sync">Always synchronously</option>
                <option value="async">Always asynchronously: 202, then the queue</option>
              </select>
            </Field>
            <Field label="When async work ends, tell them" hint="An outbound operation: it gets the correlation id and the outcome">
              <select value={svc.callback} onChange={(e) => setSvc({ ...svc, callback: e.target.value })} className={INPUT_CLS}>
                <option value="">Nobody - they read the status</option>
                {operations.map((o) => <option key={o.id} value={o.id}>{o.code}</option>)}
              </select>
            </Field>
            <Field label="Description" wide><input value={svc.description} onChange={(e) => setSvc({ ...svc, description: e.target.value })} className={INPUT_CLS} /></Field>
            <Field label="Their field names → ours" hint="custName => name   ·   mail => email | lower   ·   = segment | value:B2B"><textarea rows={3} value={svc.input} onChange={(e) => setSvc({ ...svc, input: e.target.value })} className={`${INPUT_CLS} font-mono text-xs`} /></Field>
            <Field label="Result fields they get back" hint="party_id => customerId. Blank: the whole result"><textarea rows={3} value={svc.output} onChange={(e) => setSvc({ ...svc, output: e.target.value })} className={`${INPUT_CLS} font-mono text-xs`} /></Field>
            <Field label="Published fields (JSON, optional)" hint='For the WSDL and OpenAPI, and refused up front if required: [{"name": "name", "type": "string", "required": true, "description": "..."}]. Types: string, number, integer, boolean, date' wide>
              <textarea rows={3} value={svc.fields} onChange={(e) => setSvc({ ...svc, fields: e.target.value })} className={`${INPUT_CLS} font-mono text-xs`} />
            </Field>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 sm:col-span-2"><input type="checkbox" checked={svc.is_active} onChange={(e) => setSvc({ ...svc, is_active: e.target.checked })} /> Active</label>
            <div className="flex gap-2 sm:col-span-2">
              <button type="button" className={PRIMARY} disabled={!svc.code.trim() || !svc.name.trim() || !svc.target} onClick={saveService}>Save service</button>
              <button type="button" className="text-xs text-slate-400" onClick={() => setSvc(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- outbound

const PATTERNS = [
  { key: "rr-sync", pattern: "request_reply", mode: "sync", reply_via: "response", title: "Request-reply, synchronous", text: "Call and wait for the answer. A credit check inside a flow." },
  { key: "ro-sync", pattern: "request_only", mode: "sync", reply_via: "response", title: "Request only, synchronous", text: "Call and wait for 'accepted'. No answer kept." },
  { key: "ro-async", pattern: "request_only", mode: "async", reply_via: "response", title: "Request only, asynchronous", text: "Queued, sent, retried. A webhook or event." },
  { key: "rr-async", pattern: "request_reply", mode: "async", reply_via: "response", title: "Request-reply, asynchronous", text: "Queued; the HTTP answer is the reply." },
  { key: "rr-callback", pattern: "request_reply", mode: "async", reply_via: "callback", title: "Request-reply, by callback", text: "They reply later to {{reply_url}}. Provisioning that takes hours." },
];

interface ConnDraft { id?: number; code: string; name: string; base_url: string; auth_type: string; auth_header: string; username: string; secret: string; token_url: string; oauth_scope: string; headers: string; timeout_seconds: number; is_active: boolean; secret_hint?: string }
const blankConn: ConnDraft = { code: "", name: "", base_url: "", auth_type: "none", auth_header: "", username: "", secret: "", token_url: "", oauth_scope: "", headers: "", timeout_seconds: 15, is_active: true };

interface OpDraft { id?: number; connection_id: number; code: string; name: string; description: string; protocol: string; method: string; path: string; headers: string; body_template: string; soap_action: string; soap_version: string; pattern: string; mode: string; reply_via: string; max_attempts: number; backoff_seconds: number; reply_timeout_minutes: number; reply_mapping: string; reply_interface_id: string; is_active: boolean }

function Outbound({ targets, operations, reloadOps }: { targets: HubTargets | null; operations: HubOperation[]; reloadOps: () => void }) {
  const [connections, setConnections] = useState<HubConnection[]>([]);
  const [conn, setConn] = useState<ConnDraft | null>(null);
  const [op, setOp] = useState<OpDraft | null>(null);
  const [testing, setTesting] = useState<{ op: HubOperation; payload: string; result?: Record<string, unknown>; busy?: boolean } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    hubApi<HubConnection[]>("/connections").then(setConnections).catch((e) => setError(clean(e)));
    reloadOps();
  }, [reloadOps]);
  useEffect(load, [load]);

  const saveConn = async () => {
    if (!conn) return;
    setError("");
    try {
      const body = { code: conn.code, name: conn.name, base_url: conn.base_url, auth_type: conn.auth_type, auth_header: conn.auth_header, username: conn.username, secret: conn.secret || null, token_url: conn.token_url,
        oauth_scope: conn.oauth_scope, headers: conn.headers.trim() ? parseJson(conn.headers, "Headers") : {}, timeout_seconds: Number(conn.timeout_seconds), is_active: conn.is_active };
      await hubApi(conn.id ? `/connections/${conn.id}` : "/connections", { method: conn.id ? "PUT" : "POST", body: JSON.stringify(body) });
      setConn(null);
      load();
    } catch (e) {
      setError(clean(e));
    }
  };

  const saveOp = async () => {
    if (!op) return;
    setError("");
    try {
      const body = { connection_id: Number(op.connection_id), code: op.code, name: op.name, description: op.description, protocol: op.protocol, method: op.method, path: op.path,
        headers: op.headers.trim() ? parseJson(op.headers, "Headers") : {}, body_template: op.body_template, soap_action: op.soap_action, soap_version: op.soap_version, pattern: op.pattern, mode: op.mode, reply_via: op.reply_via,
        max_attempts: Number(op.max_attempts), backoff_seconds: Number(op.backoff_seconds), reply_timeout_minutes: Number(op.reply_timeout_minutes), reply_mapping: op.reply_mapping.trim() ? parseJson(op.reply_mapping, "Reply mapping") : null,
        reply_interface_id: op.reply_interface_id ? Number(op.reply_interface_id) : null, is_active: op.is_active };
      await hubApi(op.id ? `/operations/${op.id}` : "/operations", { method: op.id ? "PUT" : "POST", body: JSON.stringify(body) });
      setOp(null);
      load();
    } catch (e) {
      setError(clean(e));
    }
  };

  const runTest = async () => {
    if (!testing) return;
    setTesting({ ...testing, busy: true, result: undefined });
    try {
      const result = await hubApi<Record<string, unknown>>(`/operations/${testing.op.id}/test`, { method: "POST", body: JSON.stringify({ payload: parseJson(testing.payload, "The payload") }) });
      setTesting({ ...testing, busy: false, result });
    } catch (e) {
      setTesting({ ...testing, busy: false, result: { ok: false, error: clean(e) } });
    }
  };

  const patternKey = (o: { pattern: string; mode: string; reply_via: string }) => PATTERNS.find((p) => p.pattern === o.pattern && p.mode === o.mode && p.reply_via === o.reply_via)?.key ?? "rr-sync";
  const patternTitle = (o: HubOperation) => PATTERNS.find((p) => p.key === patternKey(o))?.title ?? "";

  return (
    <div className="space-y-5">
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
      <section className={CARD}>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="flex-1 text-sm font-semibold text-slate-200">Connections - the systems BRT 360 calls</h3>
          <button type="button" className={BTN} onClick={() => setConn({ ...blankConn })}>+ New connection</button>
        </div>
        {connections.length === 0 && <p className="text-xs text-slate-500">No connection yet.</p>}
        {connections.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
            <span className="font-mono text-sky-300">{c.code}</span>
            <span className="text-slate-200">{c.name}</span>
            <span className="break-all text-xs text-slate-400">{c.base_url || "no address"} · {c.auth_type}{c.has_secret ? ` ${c.secret_hint}` : ""} · {c.timeout_seconds} s</span>
            {!c.is_active && <Badge status="cancelled" />}
            {c.circuit_open_until && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">left alone until {when(c.circuit_open_until)}</span>}
            <span className="ml-auto flex gap-2">
              {(c.circuit_open_until || c.failure_count > 0) && <button type="button" className={LINK} onClick={() => hubApi(`/connections/${c.id}/reset-breaker`, { method: "POST" }).then(load)}>Try again now</button>}
              <button type="button" className={LINK} onClick={() => setOp({ connection_id: c.id, code: "", name: "", description: "", protocol: "rest", method: "POST", path: "", headers: "", body_template: "", soap_action: "", soap_version: "1.1", pattern: "request_reply", mode: "sync", reply_via: "response", max_attempts: 5, backoff_seconds: 30, reply_timeout_minutes: 1440, reply_mapping: "", reply_interface_id: "", is_active: true })}>+ Operation</button>
              <button type="button" className={LINK} onClick={() => setConn({ id: c.id, code: c.code, name: c.name, base_url: c.base_url, auth_type: c.auth_type, auth_header: c.auth_header, username: c.username, secret: "", token_url: c.token_url, oauth_scope: c.oauth_scope, headers: Object.keys(c.headers).length ? JSON.stringify(c.headers, null, 2) : "", timeout_seconds: c.timeout_seconds, is_active: c.is_active, secret_hint: c.secret_hint })}>Edit</button>
            </span>
          </div>
        ))}
        {conn && (
          <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
            <Field label="Code"><input value={conn.code} onChange={(e) => setConn({ ...conn, code: e.target.value })} placeholder="ERP" className={INPUT_CLS} /></Field>
            <Field label="Name"><input value={conn.name} onChange={(e) => setConn({ ...conn, name: e.target.value })} placeholder="SAP ERP" className={INPUT_CLS} /></Field>
            <Field label="Address" hint="A public address. A system on a private network must first be allowed by the platform's administrator (INTEGRATION_ALLOW_PRIVATE_CIDRS)" wide><input value={conn.base_url} onChange={(e) => setConn({ ...conn, base_url: e.target.value })} placeholder="https://erp.example.com/api" className={INPUT_CLS} /></Field>
            <Field label="Sign-in">
              <select value={conn.auth_type} onChange={(e) => setConn({ ...conn, auth_type: e.target.value })} className={INPUT_CLS}>
                <option value="none">None</option><option value="basic">Basic (user name, password)</option><option value="bearer">Bearer token</option>
                <option value="api_key">API key in a header</option><option value="oauth2">OAuth2 client credentials</option><option value="hmac">HMAC signature (webhooks)</option>
              </select>
            </Field>
            <Field label="Timeout (seconds)" hint="A synchronous call gives up by 30 s whatever this says"><input type="number" min={1} max={120} value={conn.timeout_seconds} onChange={(e) => setConn({ ...conn, timeout_seconds: Number(e.target.value) })} className={INPUT_CLS} /></Field>
            {(conn.auth_type === "basic" || conn.auth_type === "oauth2") && <Field label={conn.auth_type === "basic" ? "User name" : "Client id"}><input value={conn.username} onChange={(e) => setConn({ ...conn, username: e.target.value })} className={INPUT_CLS} /></Field>}
            {conn.auth_type === "api_key" && <Field label="Header"><input value={conn.auth_header} onChange={(e) => setConn({ ...conn, auth_header: e.target.value })} placeholder="X-API-Key" className={INPUT_CLS} /></Field>}
            {conn.auth_type !== "none" && (
              <Field label={{ basic: "Password", bearer: "Token", api_key: "Key", oauth2: "Client secret", hmac: "Shared secret" }[conn.auth_type] ?? "Secret"} hint={conn.id ? `Held, encrypted: ${conn.secret_hint || "none"}. Blank keeps it` : "Stored encrypted; never shown again"}>
                <input type="password" autoComplete="new-password" value={conn.secret} onChange={(e) => setConn({ ...conn, secret: e.target.value })} className={INPUT_CLS} />
              </Field>
            )}
            {conn.auth_type === "oauth2" && (
              <>
                <Field label="Token address"><input value={conn.token_url} onChange={(e) => setConn({ ...conn, token_url: e.target.value })} placeholder="https://login.example.com/oauth/token" className={INPUT_CLS} /></Field>
                <Field label="Scope"><input value={conn.oauth_scope} onChange={(e) => setConn({ ...conn, oauth_scope: e.target.value })} className={INPUT_CLS} /></Field>
              </>
            )}
            <Field label="Headers on every call (JSON)" wide><textarea rows={2} value={conn.headers} onChange={(e) => setConn({ ...conn, headers: e.target.value })} placeholder='{"X-Tenant": "acme"}' className={`${INPUT_CLS} font-mono text-xs`} /></Field>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 sm:col-span-2"><input type="checkbox" checked={conn.is_active} onChange={(e) => setConn({ ...conn, is_active: e.target.checked })} /> Active - switched off, its queue waits</label>
            <div className="flex gap-2 sm:col-span-2">
              <button type="button" className={PRIMARY} disabled={!conn.code.trim() || !conn.name.trim()} onClick={saveConn}>Save connection</button>
              <button type="button" className="text-xs text-slate-400" onClick={() => setConn(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>

      <section className={CARD}>
        <h3 className="text-sm font-semibold text-slate-200">Operations - the calls</h3>
        {operations.length === 0 && <p className="text-xs text-slate-500">No operation yet - add one from a connection.</p>}
        {operations.map((o) => (
          <div key={o.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
            <span className="font-mono text-sky-300">{o.code}</span>
            <span className="text-slate-200">{o.name}</span>
            <span className="break-all text-xs text-slate-400">{o.connection} · {o.protocol === "soap" ? `SOAP ${o.soap_version}` : o.method} {o.path} · {patternTitle(o)}</span>
            {!o.is_active && <Badge status="cancelled" />}
            <span className="ml-auto flex gap-2">
              <button type="button" className={LINK} onClick={() => setTesting({ op: o, payload: "{\n  \n}" })}>Test</button>
              <button type="button" className={LINK} onClick={() => setOp({ id: o.id, connection_id: o.connection_id, code: o.code, name: o.name, description: o.description, protocol: o.protocol, method: o.method, path: o.path, headers: Object.keys(o.headers).length ? JSON.stringify(o.headers, null, 2) : "", body_template: o.body_template, soap_action: o.soap_action, soap_version: o.soap_version, pattern: o.pattern, mode: o.mode, reply_via: o.reply_via, max_attempts: o.max_attempts, backoff_seconds: o.backoff_seconds, reply_timeout_minutes: o.reply_timeout_minutes, reply_mapping: o.reply_mapping ? JSON.stringify(o.reply_mapping, null, 2) : "", reply_interface_id: o.reply_interface_id ? String(o.reply_interface_id) : "", is_active: o.is_active })}>Edit</button>
            </span>
          </div>
        ))}
        {testing && (
          <div className="space-y-2 rounded-lg border border-slate-700 p-3">
            <p className="text-xs text-slate-300">A <b>real call</b> to {testing.op.connection}, now - not retried, not queued. Use a test system or a harmless request.</p>
            <textarea rows={4} value={testing.payload} onChange={(e) => setTesting({ ...testing, payload: e.target.value })} className={`${INPUT_CLS} font-mono text-xs`} />
            <div className="flex gap-2">
              <button type="button" className={PRIMARY} disabled={testing.busy} onClick={runTest}>{testing.busy ? "Calling…" : "Send it"}</button>
              <button type="button" className="text-xs text-slate-400" onClick={() => setTesting(null)}>Close</button>
            </div>
            {testing.result && (
              <div className="space-y-2 text-xs">
                <p className={testing.result.ok ? "text-emerald-300" : "text-red-300"}>
                  {testing.result.ok ? `Answered ${String(testing.result.http_status)} in ${String(testing.result.latency_ms)} ms` : String(testing.result.error)}
                  {!testing.result.ok && testing.result.retryable ? " - would be retried" : ""}
                </p>
                {!!testing.result.url && <p className="break-all text-slate-400">{String(testing.result.method)} {String(testing.result.url)}</p>}
                {!!testing.result.request_body && <><p className="text-slate-400">Sent</p><Pre>{pretty(String(testing.result.request_body))}</Pre></>}
                {!!testing.result.response_body && <><p className="text-slate-400">Answer</p><Pre>{pretty(String(testing.result.response_body))}</Pre></>}
                {testing.result.reply !== undefined && testing.result.reply !== null && <><p className="text-slate-400">After the reply mapping</p><Pre>{JSON.stringify(testing.result.reply, null, 2)}</Pre></>}
              </div>
            )}
          </div>
        )}
        {op && (
          <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <p className="mb-1 text-xs text-slate-400">How it talks</p>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {PATTERNS.map((p) => (
                  <button key={p.key} type="button" onClick={() => setOp({ ...op, pattern: p.pattern, mode: p.mode, reply_via: p.reply_via })}
                    className={`rounded-lg border p-2 text-left ${patternKey(op) === p.key ? "border-sky-500 bg-sky-500/10" : "border-slate-700 hover:border-slate-500"}`}>
                    <span className="block text-xs font-semibold text-slate-200">{p.title}</span>
                    <span className="block text-[11px] text-slate-400">{p.text}</span>
                  </button>
                ))}
              </div>
            </div>
            <Field label="Code" hint="What Flow Studio and webhooks name it by"><input value={op.code} onChange={(e) => setOp({ ...op, code: e.target.value })} placeholder="CREATE_CUSTOMER" className={INPUT_CLS} /></Field>
            <Field label="Name"><input value={op.name} onChange={(e) => setOp({ ...op, name: e.target.value })} className={INPUT_CLS} /></Field>
            <Field label="Protocol">
              <select value={op.protocol} onChange={(e) => setOp({ ...op, protocol: e.target.value })} className={INPUT_CLS}><option value="rest">REST / HTTP</option><option value="soap">SOAP</option></select>
            </Field>
            {op.protocol === "rest" ? (
              <Field label="Method"><select value={op.method} onChange={(e) => setOp({ ...op, method: e.target.value })} className={INPUT_CLS}>{["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => <option key={m}>{m}</option>)}</select></Field>
            ) : (
              <Field label="SOAP version"><select value={op.soap_version} onChange={(e) => setOp({ ...op, soap_version: e.target.value })} className={INPUT_CLS}><option>1.1</option><option>1.2</option></select></Field>
            )}
            <Field label="Path" hint="Added to the connection's address. {{placeholders}} are filled from the payload" wide><input value={op.path} onChange={(e) => setOp({ ...op, path: e.target.value })} placeholder="/customers/{{customer_id}}" className={INPUT_CLS} /></Field>
            {op.protocol === "soap" && <Field label="SOAPAction" wide><input value={op.soap_action} onChange={(e) => setOp({ ...op, soap_action: e.target.value })} className={INPUT_CLS} /></Field>}
            <Field label="Body template" hint="JSON or XML with {{payload fields}}, {{correlation_id}}, {{reply_url}}, {{now}}. Values are escaped for XML; a lone placeholder keeps its type in JSON. Blank: the payload as it is" wide>
              <textarea rows={5} value={op.body_template} onChange={(e) => setOp({ ...op, body_template: e.target.value })} placeholder={'{"name": "{{name}}", "callback": "{{reply_url}}"}'} className={`${INPUT_CLS} font-mono text-xs`} />
            </Field>
            {op.pattern === "request_reply" && (
              <>
                <Field label="From the reply, keep (JSON)" hint='{"our name": "path.in.reply"}. Used as {{reply.our_name}} in a flow. Blank: all of it'><textarea rows={3} value={op.reply_mapping} onChange={(e) => setOp({ ...op, reply_mapping: e.target.value })} placeholder={'{"customer_id": "data.id"}'} className={`${INPUT_CLS} font-mono text-xs`} /></Field>
                <Field label="Load the reply's records through" hint="A BIM interface - keep your data in step with theirs">
                  <select value={op.reply_interface_id} onChange={(e) => setOp({ ...op, reply_interface_id: e.target.value })} className={INPUT_CLS}>
                    <option value="">Don't load it</option>
                    {targets?.interfaces.map((i) => <option key={i.code} value={String(i.id)}>{i.code}</option>)}
                  </select>
                </Field>
              </>
            )}
            {op.mode === "async" && (
              <>
                <Field label="Attempts" hint="Then the dead letters"><input type="number" min={1} max={20} value={op.max_attempts} onChange={(e) => setOp({ ...op, max_attempts: Number(e.target.value) })} className={INPUT_CLS} /></Field>
                <Field label="First retry after (seconds)" hint="Doubles each time, to an hour at most"><input type="number" min={1} max={3600} value={op.backoff_seconds} onChange={(e) => setOp({ ...op, backoff_seconds: Number(e.target.value) })} className={INPUT_CLS} /></Field>
              </>
            )}
            {op.reply_via === "callback" && <Field label="Wait for the reply (minutes)" hint="Then it times out"><input type="number" min={1} value={op.reply_timeout_minutes} onChange={(e) => setOp({ ...op, reply_timeout_minutes: Number(e.target.value) })} className={INPUT_CLS} /></Field>}
            <Field label="Headers (JSON)" wide><textarea rows={2} value={op.headers} onChange={(e) => setOp({ ...op, headers: e.target.value })} className={`${INPUT_CLS} font-mono text-xs`} /></Field>
            <label className="flex items-center gap-1.5 text-xs text-slate-300 sm:col-span-2"><input type="checkbox" checked={op.is_active} onChange={(e) => setOp({ ...op, is_active: e.target.checked })} /> Active</label>
            <div className="flex gap-2 sm:col-span-2">
              <button type="button" className={PRIMARY} disabled={!op.code.trim() || !op.name.trim()} onClick={saveOp}>Save operation</button>
              <button type="button" className="text-xs text-slate-400" onClick={() => setOp(null)}>Cancel</button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- webhooks

function Webhooks({ targets, operations }: { targets: HubTargets | null; operations: HubOperation[] }) {
  const [subs, setSubs] = useState<HubSubscription[]>([]);
  const [draft, setDraft] = useState<{ id?: number; name: string; event_pattern: string; operation_id: string; is_active: boolean } | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => { hubApi<HubSubscription[]>("/subscriptions").then(setSubs).catch((e) => setError(clean(e))); }, []);
  useEffect(load, [load]);
  const save = async () => {
    if (!draft) return;
    setError("");
    try {
      await hubApi(draft.id ? `/subscriptions/${draft.id}` : "/subscriptions", { method: draft.id ? "PUT" : "POST", body: JSON.stringify({ name: draft.name, event_pattern: draft.event_pattern, operation_id: Number(draft.operation_id), is_active: draft.is_active }) });
      setDraft(null);
      load();
    } catch (e) {
      setError(clean(e));
    }
  };
  return (
    <section className={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="flex-1 text-sm font-semibold text-slate-200">Webhooks - when this happens, make that call</h3>
        <button type="button" className={BTN} onClick={() => setDraft({ name: "", event_pattern: "", operation_id: "", is_active: true })}>+ New webhook</button>
      </div>
      <p className="text-xs text-slate-400">Each event goes out once, as a CloudEvents 1.0 document, through the operation you choose. Make it an asynchronous operation so it retries; give its connection HMAC sign-in so the receiver can verify it came from you. Only events from now on are sent.</p>
      {error && <p className="text-sm text-red-300">{error}</p>}
      {subs.length === 0 && <p className="text-xs text-slate-500">No webhook yet.</p>}
      {subs.map((s) => (
        <div key={s.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm">
          <span className="text-slate-200">{s.name}</span>
          <span className="text-xs text-slate-400"><Code>{s.event_pattern}</Code> → <span className="font-mono">{s.operation}</span> · {s.messages} sent</span>
          {!s.is_active && <Badge status="cancelled" />}
          <button type="button" className={`ml-auto ${LINK}`} onClick={() => setDraft({ id: s.id, name: s.name, event_pattern: s.event_pattern, operation_id: String(s.operation_id), is_active: s.is_active })}>Edit</button>
        </div>
      ))}
      {draft && (
        <div className="grid gap-2 rounded-lg border border-slate-700 p-3 sm:grid-cols-2">
          <Field label="Name"><input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Orders to the ERP" className={INPUT_CLS} /></Field>
          <Field label="Call">
            <select value={draft.operation_id} onChange={(e) => setDraft({ ...draft, operation_id: e.target.value })} className={INPUT_CLS}>
              <option value="">Choose an operation…</option>
              {operations.map((o) => <option key={o.id} value={o.id}>{o.code} ({o.mode})</option>)}
            </select>
          </Field>
          <Field label="Events" hint="An exact event, or a pattern: order.*  ·  flow.executed  ·  * for everything" wide>
            <input list="hub-events" value={draft.event_pattern} onChange={(e) => setDraft({ ...draft, event_pattern: e.target.value })} placeholder="order.*" className={INPUT_CLS} />
            <datalist id="hub-events">{targets?.events.map((e) => <option key={e} value={e} />)}</datalist>
          </Field>
          <label className="flex items-center gap-1.5 text-xs text-slate-300 sm:col-span-2"><input type="checkbox" checked={draft.is_active} onChange={(e) => setDraft({ ...draft, is_active: e.target.checked })} /> Active</label>
          <div className="flex gap-2 sm:col-span-2">
            <button type="button" className={PRIMARY} disabled={!draft.name.trim() || !draft.event_pattern.trim() || !draft.operation_id} onClick={save}>Save webhook</button>
            <button type="button" className="text-xs text-slate-400" onClick={() => setDraft(null)}>Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- messages

const STATUSES = ["queued", "sending", "processing", "awaiting_reply", "delivered", "replied", "succeeded", "failed", "dead", "timed_out", "cancelled"];

function Messages({ initialStatus }: { initialStatus: string }) {
  const [rows, setRows] = useState<HubMessage[]>([]);
  const [direction, setDirection] = useState("");
  const [status, setStatus] = useState(initialStatus);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<HubMessage | null>(null);
  const [error, setError] = useState("");
  useEffect(() => setStatus(initialStatus), [initialStatus]);

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: "100" });
    if (direction) params.set("direction", direction);
    if (status) params.set("status", status);
    if (q.trim()) params.set("q", q.trim());
    hubApi<HubMessage[]>(`/messages?${params}`).then(setRows).catch((e) => setError(clean(e)));
  }, [direction, status, q]);
  useEffect(() => {
    const handle = window.setTimeout(load, q ? 300 : 0);
    const timer = window.setInterval(load, 10000);
    return () => { window.clearTimeout(handle); window.clearInterval(timer); };
  }, [load, q]);

  const act = async (row: HubMessage, action: "replay" | "cancel") => {
    setError("");
    try {
      await hubApi(`/messages/${row.id}/${action}`, { method: "POST" });
      setOpen(null);
      load();
    } catch (e) {
      setError(clean(e));
    }
  };
  const show = (id: number) => hubApi<HubMessage>(`/messages/${id}`).then(setOpen).catch((e) => setError(clean(e)));

  return (
    <section className={CARD}>
      <div className="flex flex-wrap gap-2">
        <select value={direction} onChange={(e) => setDirection(e.target.value)} className={`${INPUT_CLS} w-auto`}><option value="">Both ways</option><option value="in">Inbound</option><option value="out">Outbound</option></select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className={`${INPUT_CLS} w-auto`}><option value="">Any status</option>{STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}</select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Correlation id, service, address, error…" className={`${INPUT_CLS} min-w-48 flex-1`} />
        <button type="button" className={BTN} onClick={load}>Refresh</button>
      </div>
      {error && <p className="text-sm text-red-300">{error}</p>}
      {rows.length === 0 && <p className="text-xs text-slate-500">No messages.</p>}
      <div className="space-y-1">
        {rows.map((m) => (
          <button key={m.id} type="button" onClick={() => show(m.id)} className="flex w-full flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-1.5 text-left text-xs hover:border-sky-500">
            <span className="w-6 text-slate-400" title={m.direction === "in" ? "Inbound" : "Outbound"}>{m.direction === "in" ? "⬇" : "⬆"}</span>
            <Badge status={m.status} />
            <span className="font-mono text-sky-300">{m.service_code ?? m.operation ?? m.kind}</span>
            <span className="text-slate-400">{m.client ?? m.source}</span>
            {m.http_status != null && <span className="text-slate-300">{m.http_status}</span>}
            {m.latency_ms != null && <span className="text-slate-500">{m.latency_ms} ms</span>}
            {m.attempts > 1 && <span className="text-slate-500">try {m.attempts}/{m.max_attempts}</span>}
            {m.error && <span className="min-w-0 flex-1 truncate text-red-300">{m.error}</span>}
            <span className="ml-auto text-slate-500">{when(m.created_at)}</span>
          </button>
        ))}
      </div>
      {open && (
        <div className="space-y-2 rounded-lg border border-slate-700 p-3 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <Badge status={open.status} />
            <span className="font-mono break-all text-slate-300">{open.correlation_id}</span>
            <span className="ml-auto flex gap-2">
              {["failed", "dead", "timed_out", "cancelled"].includes(open.status) && (open.direction === "out" || open.mode === "async") && <button type="button" className={BTN} onClick={() => act(open, "replay")}>Send again</button>}
              {["queued", "awaiting_reply", "dead"].includes(open.status) && <button type="button" className={BTN} onClick={() => act(open, "cancel")}>Cancel</button>}
              <button type="button" className="text-slate-400" onClick={() => setOpen(null)}>Close</button>
            </span>
          </div>
          <p className="text-slate-400">
            {open.direction === "in" ? "Inbound" : "Outbound"} · {open.protocol} · {open.pattern?.replace("_", "-")} · {open.mode} · {open.source}
            {open.next_attempt_at && open.status === "queued" ? ` · next try ${when(open.next_attempt_at)}` : ""}
            {open.reply_deadline && open.status === "awaiting_reply" ? ` · waiting until ${when(open.reply_deadline)}` : ""}
          </p>
          {open.url && <p className="break-all text-slate-300">{open.http_method} {open.url}</p>}
          {open.error && <p className="text-red-300">{open.error}</p>}
          {!!open.request_headers && Object.keys(open.request_headers).length > 0 && <><p className="text-slate-400">Headers (credentials hidden)</p><Pre>{JSON.stringify(open.request_headers, null, 2)}</Pre></>}
          {!!(open.request_body || open.payload) && <><p className="text-slate-400">{open.direction === "in" ? "What came in" : "What went out"}</p><Pre>{pretty(open.request_body || open.payload)}</Pre></>}
          {!!open.response_body && <><p className="text-slate-400">{open.direction === "in" ? "What it was answered" : "What came back"}</p><Pre>{pretty(open.response_body)}</Pre></>}
          {!!open.reply && open.direction === "out" && <><p className="text-slate-400">The reply, mapped</p><Pre>{pretty(open.reply)}</Pre></>}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- the page

type Tab = "overview" | "inbound" | "outbound" | "webhooks" | "messages";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "inbound", label: "Inbound" }, { id: "outbound", label: "Outbound" }, { id: "webhooks", label: "Webhooks" }, { id: "messages", label: "Messages" },
];

export default function AdminIntegrationHub() {
  const [tab, setTab] = useState<Tab>("overview");
  const [status, setStatus] = useState("");
  const [targets, setTargets] = useState<HubTargets | null>(null);
  const [operations, setOperations] = useState<HubOperation[]>([]);
  const reloadOps = useCallback(() => { hubApi<HubOperation[]>("/operations").then(setOperations).catch(() => null); }, []);
  useEffect(() => {
    hubApi<HubTargets>("/targets").then(setTargets).catch(() => null);
    reloadOps();
  }, [reloadOps, tab]);
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Integration Hub</h2>
        <p className="text-sm text-slate-400">How BRT 360 talks to other systems, both ways - REST and SOAP, synchronous and asynchronous, with every message logged. Bulk data has its own home in Integration (BIM).</p>
      </div>
      <div role="tablist" className="flex flex-wrap gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} type="button" onClick={() => { setTab(t.id); if (t.id !== "messages") setStatus(""); }}
            className={`rounded-t-lg px-3 py-1.5 text-sm font-medium ${tab === t.id ? "border-b-2 border-sky-500 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Overview goMessages={(s) => { setStatus(s); setTab("messages"); }} />}
      {tab === "inbound" && <Inbound targets={targets} operations={operations} />}
      {tab === "outbound" && <Outbound targets={targets} operations={operations} reloadOps={reloadOps} />}
      {tab === "webhooks" && <Webhooks targets={targets} operations={operations} />}
      {tab === "messages" && <Messages initialStatus={status} />}
    </div>
  );
}
