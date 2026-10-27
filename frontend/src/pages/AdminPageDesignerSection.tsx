import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addPageField,
  applyPageOps,
  compilePageRule,
  createPage,
  deletePageRule,
  listPageRules,
  listPages,
  pageCatalog,
  planPage,
  resetPage,
  savePage,
  savePageRule,
  togglePageRule,
  type CatalogField,
  type DesignedPage,
  type PageForm,
  type PageLayout,
  type PageOp,
  type PageRule,
  type PlacedField,
  copyPage,
  isElement,
  setPageActive,
} from "../api/client";
import { PageFormView } from "../components/pages/PageFields";

// ------------------------------------------------------------------ look & feel

const INPUT = "w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100 outline-none transition focus:border-sky-500 focus:ring-1 focus:ring-sky-500/40";
const LABEL = "mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500";
const GHOST = "rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-800 hover:text-white disabled:opacity-30 disabled:hover:bg-transparent";
const PRIMARY = "rounded-md bg-sky-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-sky-500 disabled:opacity-40";
const TYPE: Record<string, { icon: string; label: string; mock: string }> = {
  text: { icon: "Aa", label: "Text", mock: "" },
  textarea: { icon: "¶", label: "Long text", mock: "" },
  number: { icon: "#", label: "Number", mock: "0" },
  boolean: { icon: "◐", label: "Yes / No", mock: "" },
  date: { icon: "▦", label: "Date", mock: "dd / mm / yyyy" },
  picklist: { icon: "▾", label: "Dropdown", mock: "Select…" },
  email: { icon: "@", label: "Email", mock: "name@company.com" },
  phone: { icon: "☏", label: "Phone", mock: "+91 …" },
};
const ENTITY_ICON: Record<string, string> = { party: "👤", lead: "🌱", opportunity: "💼", quote: "🧾", order: "📦" };
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const clean = (e: unknown) => String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");

type Selection = { kind: "page" } | { kind: "section"; s: number } | { kind: "field"; s: number; f: number };
type Drag = { from: "palette"; key: string } | { from: "canvas"; s: number; f: number };

function Switch({ on, onChange, label, hint }: { on: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <button type="button" onClick={() => onChange(!on)} className="flex w-full items-center gap-3 rounded-md px-1 py-1.5 text-left hover:bg-slate-800/50">
      <span className={`relative h-5 w-9 shrink-0 rounded-full transition ${on ? "bg-sky-500" : "bg-slate-700"}`}>
        <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm text-slate-200">{label}</span>
        {hint && <span className="block text-[11px] text-slate-500">{hint}</span>}
      </span>
    </button>
  );
}

function TypeIcon({ type }: { type: string }) {
  return <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded bg-slate-800 font-mono text-[11px] text-slate-300">{TYPE[type]?.icon ?? "?"}</span>;
}

/** What a field looks like on the canvas - the real control, inert. */
function MockControl({ field }: { field: CatalogField }) {
  if (field.data_type === "boolean")
    return (
      <div className="flex gap-1.5">
        {["Yes", "No"].map((v) => (
          <span key={v} className="rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-500">
            {v}
          </span>
        ))}
      </div>
    );
  if (field.data_type === "picklist" && field.values.length <= 4)
    return (
      <div className="flex flex-wrap gap-1.5">
        {field.values.map((v) => (
          <span key={v} className="rounded-md border border-slate-700 px-2.5 py-1 text-xs text-slate-500">
            {v}
          </span>
        ))}
      </div>
    );
  return (
    <div className={`flex items-center justify-between rounded-md border border-slate-700/80 bg-slate-950/70 px-2.5 text-xs text-slate-600 ${field.data_type === "textarea" ? "h-14 items-start pt-1.5" : "h-8"}`}>
      <span>{field.data_type === "picklist" ? `${field.values.length} choices…` : TYPE[field.data_type]?.mock}</span>
      {field.data_type === "picklist" && <span>▾</span>}
      {field.data_type === "date" && <span>▦</span>}
    </div>
  );
}

function previewForm(page: DesignedPage, layout: PageLayout, catalog: CatalogField[], rules: PageRule[]): PageForm {
  const byKey = Object.fromEntries(catalog.map((f) => [f.key, f]));
  return {
    page_key: page.page_key, name: page.name, entity_type: page.entity_type, segment: page.segment,
    sections: layout.sections
      .map((s) => ({
        title: s.title, columns: s.columns ?? 2,
        fields: s.fields
          .filter((f) => isElement(f) || (!f.hidden && byKey[f.key]))
          .map((f) =>
            isElement(f)
              ? {
                  key: f.key, kind: f.kind, label: f.label ?? "", type: "text" as const, values: [], mandatory: false, readonly: true, help: "",
                  core: false, value: null, intent: f.intent, style: f.style, text: f.text, span: "full" as const,
                }
              : {
                  key: f.key, label: f.label || byKey[f.key].label, type: byKey[f.key].data_type, values: byKey[f.key].values, mandatory: !!f.mandatory,
                  readonly: !!f.readonly, help: f.help || byKey[f.key].help, core: byKey[f.key].core, value: null,
                  span: f.span ?? (byKey[f.key].data_type === "textarea" ? "full" : null),
                }
          ),
      }))
      .filter((s) => s.fields.length),
    rules: rules
      .filter((r) => r.kind === "simple" && r.is_active && r.entity_type === page.entity_type && (!r.segment || !page.segment || r.segment === page.segment))
      .map((r) => ({ id: r.id, name: r.name, compiled: r.compiled, message: r.message })),
  };
}

const ruleFields = (r: PageRule) => [
  ...(r.compiled.when ?? []).map((c) => c.field), ...(r.compiled.require ?? []), ...(r.compiled.check ?? []).map((c) => c.field),
];

// ------------------------------------------------------------------ rules drawer

function RulesDrawer({ page, rules, flows, sample, initial, onClose, reload }: {
  page: DesignedPage; rules: PageRule[]; flows: { id: number; name: string; is_active: boolean }[]; sample: Record<string, unknown>;
  initial: string; onClose: () => void; reload: () => void;
}) {
  const [text, setText] = useState(initial);
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState<{ explanation: string; engine: string; test?: string[] } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [flowId, setFlowId] = useState("");
  const mine = rules.filter((r) => r.entity_type === page.entity_type && (!r.segment || !page.segment || r.segment === page.segment));
  const examples = page.entity_type === "party"
    ? ["GSTIN must be a valid GSTIN", "if Employees is 5000+ then PAN is mandatory", "PIN code must be 6 digits", "Date of birth must be in the past"]
    : ["if Budget is more than 100000 then Decision timeline is mandatory", "Expected close date must be in the future"];

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(clean(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-950/60 backdrop-blur-[1px]" onClick={onClose}>
      <aside className="flex h-full w-full max-w-md flex-col border-l border-slate-800 bg-slate-900 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center gap-3 border-b border-slate-800 px-5 py-4">
          <div className="flex-1">
            <h3 className="text-base font-semibold text-slate-100">Validation rules</h3>
            <p className="text-xs text-slate-500">Checked as users type and again on every save</p>
          </div>
          <button type="button" onClick={onClose} className={GHOST}>
            ✕
          </button>
        </header>
        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <section className="space-y-2">
            <label className={LABEL}>Describe a rule</label>
            <textarea rows={3} value={text} onChange={(e) => (setText(e.target.value), setPreview(null))} placeholder={`e.g. ${examples[0]}`} className={`${INPUT} resize-none`} />
            <div className="flex flex-wrap gap-1">
              {examples.map((x) => (
                <button key={x} type="button" onClick={() => (setText(x), setPreview(null))} className="rounded-full bg-slate-800 px-2.5 py-0.5 text-[11px] text-slate-400 hover:text-sky-300">
                  {x}
                </button>
              ))}
            </div>
            <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Message users see (optional)" className={INPUT} />
            {preview && (
              <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
                <b>Understood{preview.engine === "ai" ? " by AI" : ""}:</b> {preview.explanation}
                {preview.test && <p className="mt-1 text-slate-300">On the preview's values: {preview.test.length ? <span className="text-amber-300">{preview.test.join("; ")}</span> : "passes"}</p>}
              </div>
            )}
            {error && <p className="text-xs text-red-400">{error}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" disabled={!text.trim() || busy} className={GHOST}
                onClick={() => run(async () => setPreview(await compilePageRule({ entity_type: page.entity_type, segment: page.segment, text, values: sample })))}>
                Check
              </button>
              <button type="button" disabled={!text.trim() || busy} className={PRIMARY}
                onClick={() => run(async () => (await savePageRule({ entity_type: page.entity_type, segment: page.segment, text, message }), setText(""), setMessage(""), setPreview(null), reload()))}>
                Add rule
              </button>
            </div>
          </section>

          <section className="space-y-2 border-t border-slate-800 pt-4">
            <label className={LABEL}>Complex check · Flow Studio</label>
            <p className="text-xs text-slate-500">A flow with the <b>Validation</b> trigger - conditions, AI decisions and a Reject node whose message stops the save.</p>
            <div className="flex gap-2">
              <select value={flowId} onChange={(e) => setFlowId(e.target.value)} className={INPUT}>
                <option value="">{flows.length ? "Choose a validation flow…" : "No validation flows yet"}</option>
                {flows.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                    {f.is_active ? "" : " (off)"}
                  </option>
                ))}
              </select>
              <button type="button" disabled={!flowId || busy} className={PRIMARY}
                onClick={() => run(async () => (await savePageRule({ entity_type: page.entity_type, segment: page.segment, flow_id: Number(flowId), message }), setFlowId(""), reload()))}>
                Add
              </button>
            </div>
          </section>

          <section className="space-y-2 border-t border-slate-800 pt-4">
            <label className={LABEL}>On this page · {mine.length}</label>
            {mine.length === 0 && <p className="text-xs text-slate-500">No rules yet.</p>}
            {mine.map((r) => (
              <div key={r.id} className={`group rounded-md border border-slate-800 bg-slate-950/50 px-3 py-2 ${r.is_active ? "" : "opacity-50"}`}>
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 text-xs">{r.kind === "flow" ? "🔀" : "✓"}</span>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-slate-200">{r.explanation}</p>
                    <p className="mt-0.5 text-[10px] text-slate-500">
                      {r.segment ? `${r.segment} only` : "All segments"}
                      {r.message ? ` · "${r.message}"` : ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
                    <button type="button" onClick={() => togglePageRule(r.id, !r.is_active).then(reload)} className="text-[10px] text-slate-400 hover:text-sky-300">
                      {r.is_active ? "Off" : "On"}
                    </button>
                    <button type="button" onClick={() => deletePageRule(r.id).then(reload)} className="text-[10px] text-slate-400 hover:text-red-400">
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </section>
        </div>
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ the designer

export default function AdminPageDesignerSection() {
  const [pages, setPages] = useState<DesignedPage[]>([]);
  const [pagesLoaded, setPagesLoaded] = useState(false);
  const [entities, setEntities] = useState<Record<string, string>>({});
  const [segments, setSegments] = useState<string[]>([]);
  const [current, setCurrent] = useState<DesignedPage | null>(null);
  const [layout, setLayout] = useState<PageLayout | null>(null);
  const [past, setPast] = useState<PageLayout[]>([]);
  const [future, setFuture] = useState<PageLayout[]>([]);
  const [saved, setSaved] = useState<string>("");
  const [catalog, setCatalog] = useState<CatalogField[]>([]);
  const [rules, setRules] = useState<PageRule[]>([]);
  const [flows, setFlows] = useState<{ id: number; name: string; is_active: boolean }[]>([]);
  const [sel, setSel] = useState<Selection>({ kind: "page" });
  const [mode, setMode] = useState<"design" | "preview">("design");
  const [device, setDevice] = useState<"desktop" | "tablet" | "phone">("desktop");
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dropAt, setDropAt] = useState<{ s: number; f: number } | null>(null);
  const [paletteQ, setPaletteQ] = useState("");
  const [ai, setAi] = useState("");
  const [plan, setPlan] = useState<{ ops: PageOp[]; preview: string[]; engine: string } | null>(null);
  const [picked, setPicked] = useState<boolean[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const [menu, setMenu] = useState(false);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [newField, setNewField] = useState<{ label: string; data_type: string; values: string; segmentOnly: boolean } | null>(null);
  const [newPage, setNewPage] = useState<{ entity_type: string; segment: string; name: string; instruction: string } | null>(null);
  const [sample, setSample] = useState<Record<string, unknown>>({});
  // what buttons / page events can run, and the roles a page can be given to
  const [runnable, setRunnable] = useState<{ code: string; label: string; flow: boolean }[]>([]);
  const [roles, setRoles] = useState<{ id: number; name: string }[]>([]);
  const [copyName, setCopyName] = useState<string | null>(null);
  const toastTimer = useRef<number | undefined>(undefined);

  const notify = useCallback((tone: "ok" | "err", text: string) => {
    setToast({ tone, text });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), tone === "ok" ? 3000 : 6000);
  }, []);

  const loadRules = useCallback(() => {
    listPageRules().then((r) => (setRules(r.rules), setFlows(r.flows))).catch(() => null);
  }, []);

  const open = useCallback((p: DesignedPage) => {
    setCurrent(p);
    setLayout(clone(p.layout));
    setSaved(JSON.stringify(p.layout));
    setPast([]);
    setFuture([]);
    setSel({ kind: "page" });
    setPlan(null);
    setSample({});
    pageCatalog(p.entity_type, p.segment).then(setCatalog).catch((e) => notify("err", clean(e)));
  }, [notify]);

  const loadPages = useCallback((keep?: string) => {
    listPages()
      .then((r) => {
        setPages(r.pages);
        setEntities(Object.fromEntries(r.entities.map((e) => [e.key, e.label])));
        setSegments(r.segments);
        setRunnable(r.runnable ?? []);
        setRoles(r.roles ?? []);
        const target = r.pages.find((p) => p.page_key === keep) ?? r.pages[0];
        if (target) open(target);
        else { setCurrent(null); setLayout(null); }
        setPagesLoaded(true);
      })
      .catch((e) => notify("err", clean(e)));
  }, [open, notify]);

  useEffect(() => {
    loadPages();
    loadRules();
  }, [loadPages, loadRules]);

  const dirty = !!layout && JSON.stringify(layout) !== saved;
  const byKey = useMemo(() => Object.fromEntries(catalog.map((f) => [f.key, f])), [catalog]);
  const placed = useMemo(() => new Set((layout?.sections ?? []).flatMap((s) => s.fields.filter((f) => !isElement(f)).map((f) => f.key))), [layout]);
  const form = useMemo(() => (current && layout ? previewForm(current, layout, catalog, rules) : null), [current, layout, catalog, rules]);
  const pageRules = rules.filter((r) => current && r.entity_type === current.entity_type && (!r.segment || !current.segment || r.segment === current.segment));

  // every edit is a new layout; the one before goes on the undo stack
  const change = useCallback((fn: (l: PageLayout) => void) => {
    if (!layout) return;
    const next = clone(layout);
    fn(next);
    setPast((p) => [...p.slice(-49), layout]);
    setFuture([]);
    setLayout(next);
  }, [layout]);
  const undo = useCallback(() => {
    if (!past.length || !layout) return;
    setFuture((f) => [layout, ...f]);
    setLayout(past[past.length - 1]);
    setPast(past.slice(0, -1));
  }, [past, layout]);
  const redo = useCallback(() => {
    if (!future.length || !layout) return;
    setPast((p) => [...p, layout]);
    setLayout(future[0]);
    setFuture(future.slice(1));
  }, [future, layout]);

  const save = useCallback(async () => {
    if (!current || !layout || !dirty) return;
    setBusy(true);
    try {
      const out = await savePage(current.page_key, layout);
      setPages((ps) => ps.map((p) => (p.page_key === out.page_key ? out : p)));
      setCurrent(out);
      setLayout(clone(out.layout));
      setSaved(JSON.stringify(out.layout));
      notify("ok", "Page saved - users see it straight away");
    } catch (e) {
      notify("err", clean(e));
    } finally {
      setBusy(false);
    }
  }, [current, layout, dirty, notify]);

  // keyboard: undo / redo / save / delete
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select, [contenteditable]");
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      } else if (!typing && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (!typing && (e.key === "Delete" || e.key === "Backspace") && sel.kind === "field") {
        e.preventDefault();
        change((l) => l.sections[sel.s].fields.splice(sel.f, 1));
        setSel({ kind: "section", s: sel.s });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, undo, redo, sel, change]);

  const switchTo = (p: DesignedPage) => {
    setMenu(false);
    if (dirty && !window.confirm("You have unsaved changes on this page. Leave without saving?")) return;
    open(p);
  };

  const place = (key: string, s: number, f: number | null) => {
    change((l) => {
      const list = l.sections[s].fields;
      const id = `${key.slice(2)}_${Date.now().toString(36)}`;
      const item: PlacedField =
        key === "__button"
          ? { key: `button:${id}`, kind: "button", id, label: "New button", intent: runnable.find((r) => r.flow)?.code ?? runnable[0]?.code, style: "default" }
          : key === "__text"
            ? { key: `text:${id}`, kind: "text", id, text: "Write a note, heading or instruction…" }
            : { key, mandatory: false, hidden: false, readonly: false };
      list.splice(f ?? list.length, 0, item);
    });
    setSel({ kind: "field", s, f: f ?? (layout?.sections[s].fields.length ?? 0) });
  };

  const onDrop = (s: number, f: number | null) => {
    if (!drag) return;
    if (drag.from === "palette") place(drag.key, s, f);
    else {
      change((l) => {
        const [item] = l.sections[drag.s].fields.splice(drag.f, 1);
        let at = f ?? l.sections[s].fields.length;
        if (drag.s === s && f !== null && drag.f < f) at -= 1;
        l.sections[s].fields.splice(at, 0, item);
      });
      setSel({ kind: "page" });
    }
    setDrag(null);
    setDropAt(null);
  };

  const askAi = async () => {
    if (!current || !ai.trim()) return;
    setBusy(true);
    try {
      if (dirty && layout) await save();
      const p = await planPage(current.page_key, ai);
      if (!p.ops.length) notify("err", "I couldn't work out a change - try \"make GSTIN mandatory\" or \"add Payment terms as a dropdown with values 30 days, 60 days\"");
      setPlan(p);
      setPicked(p.ops.map(() => true));
    } catch (e) {
      notify("err", clean(e));
    } finally {
      setBusy(false);
    }
  };

  const applyPlan = async () => {
    if (!current || !plan) return;
    setBusy(true);
    try {
      const out = await applyPageOps(current.page_key, plan.ops.filter((_, i) => picked[i]));
      setPlan(null);
      setAi("");
      setPages((ps) => ps.map((p) => (p.page_key === out.page_key ? out : p)));
      open(out);
      notify("ok", "The AI's changes are applied and saved");
    } catch (e) {
      notify("err", clean(e));
    } finally {
      setBusy(false);
    }
  };

  const createField = async () => {
    if (!current || !newField) return;
    setBusy(true);
    try {
      const target = sel.kind !== "page" ? sel.s : Math.max(0, (layout?.sections.length ?? 1) - 1);
      const created = await addPageField({
        entity_type: current.entity_type, label: newField.label.trim(), data_type: newField.data_type,
        values: newField.values.split(",").map((v) => v.trim()).filter(Boolean), segment: newField.segmentOnly ? current.segment : null,
        section: layout?.sections[target]?.title ?? "More details",
      });
      setCatalog((c) => [...c, created]);
      place(created.key, target, null);
      setNewField(null);
      notify("ok", `"${created.label}" added to the page - save to keep it`);
    } catch (e) {
      notify("err", clean(e));
    } finally {
      setBusy(false);
    }
  };

  const makePage = async () => {
    if (!newPage) return;
    setBusy(true);
    try {
      const made = await createPage({ entity_type: newPage.entity_type, segment: newPage.segment || null, name: newPage.name, instruction: newPage.instruction });
      setNewPage(null);
      loadPages(made.page_key);
      notify("ok", `Created "${made.name}"`);
    } catch (e) {
      notify("err", clean(e));
    } finally {
      setBusy(false);
    }
  };

  // palette: fields not on the page, grouped
  const q = paletteQ.trim().toLowerCase();
  const available = catalog.filter((f) => f.is_active && !placed.has(f.key) && (!q || `${f.label} ${f.key}`.toLowerCase().includes(q)));
  const groups: [string, CatalogField[]][] = [
    ["Core", available.filter((f) => f.core)],
    ["Standard", available.filter((f) => !f.core && f.system)],
    ["Custom", available.filter((f) => !f.core && !f.system)],
  ];

  const selField = sel.kind === "field" && layout ? layout.sections[sel.s]?.fields[sel.f] : null;
  const selDef = selField ? byKey[selField.key] : null;
  const setField = (patch: Partial<PlacedField>) => {
    if (sel.kind !== "field") return;
    change((l) => {
      const f = l.sections[sel.s].fields[sel.f];
      Object.assign(f, patch);
      if (patch.hidden) f.mandatory = false;
      if (patch.mandatory) f.hidden = false;
      for (const k of Object.keys(f) as (keyof PlacedField)[]) if ((f[k] === undefined || f[k] === "") && !(isElement(f) && (k === "label" || k === "text"))) delete f[k];
    });
  };
  const stats = layout
    ? {
        fields: layout.sections.reduce((n, s) => n + s.fields.filter((f) => !f.hidden && !isElement(f)).length, 0),
        mandatory: layout.sections.reduce((n, s) => n + s.fields.filter((f) => f.mandatory).length, 0),
        hidden: layout.sections.reduce((n, s) => n + s.fields.filter((f) => f.hidden).length, 0),
      }
    : null;
  const width = { desktop: "max-w-4xl", tablet: "max-w-[768px]", phone: "max-w-[390px]" }[device];

  if (!current || !layout) return <p className="text-sm text-slate-500">{pagesLoaded ? "No page designs are available for the selected modules. Activate a functional module in Platform Admin." : "Loading the Page Designer…"}</p>;

  return (
    <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950 shadow-xl">
      {/* ---------------- toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-2">
        <div className="relative">
          <button type="button" onClick={() => setMenu(!menu)} className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-100 hover:border-slate-500">
            <span>{ENTITY_ICON[current.entity_type]}</span>
            <span className="font-semibold">{current.is_default ? entities[current.entity_type] : current.name}</span>
            {current.segment && <span className="rounded bg-sky-500/15 px-1.5 text-[11px] font-semibold text-sky-300">{current.segment}</span>}
            <span className="text-slate-500">▾</span>
          </button>
          {menu && (
            <div className="absolute left-0 z-30 mt-1 max-h-[70vh] w-72 overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 p-1 shadow-2xl">
              {Object.entries(entities).map(([key, label]) => {
                const list = pages.filter((p) => p.entity_type === key);
                if (!list.length) return null;
                return (
                  <div key={key} className="py-1">
                    <p className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                      {ENTITY_ICON[key]} {label}
                    </p>
                    {list.map((p) => (
                      <button key={p.page_key} type="button" onClick={() => switchTo(p)}
                        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${p.page_key === current.page_key ? "bg-sky-500/15 text-sky-200" : "text-slate-300 hover:bg-slate-800"}`}>
                        <span className="flex-1 truncate">{p.is_default ? (p.segment ? `${p.segment} page` : "Standard page") : p.name}</span>
                        {!p.is_active && <span className="rounded bg-slate-800 px-1 text-[9px] text-slate-400">off</span>}
                        {(p.role_ids ?? []).length > 0 && <span className="text-[10px] text-violet-300" title="Given to roles">👥</span>}
                        {p.customized && <span className="h-1.5 w-1.5 rounded-full bg-sky-400" title="Customized" />}
                      </button>
                    ))}
                  </div>
                );
              })}
              <button type="button" onClick={() => (setMenu(false), setNewPage({ entity_type: current.entity_type, segment: current.segment ?? "", name: "", instruction: "" }))}
                className="mt-1 w-full rounded-md border-t border-slate-800 px-2 py-2 text-left text-sm text-sky-400 hover:bg-slate-800">
                + New page…
              </button>
            </div>
          )}
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[11px] ${current.customized ? "bg-sky-500/10 text-sky-300" : "bg-slate-800 text-slate-400"}`}>
          {current.customized ? "Customized" : "Standard layout"}
        </span>

        <div className="mx-auto flex items-center rounded-md border border-slate-700 bg-slate-950 p-0.5">
          {(["design", "preview"] as const).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)} className={`rounded px-3 py-1 text-xs font-semibold capitalize ${mode === m ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"}`}>
              {m}
            </button>
          ))}
        </div>
        {mode === "preview" && (
          <div className="flex items-center rounded-md border border-slate-700 bg-slate-950 p-0.5">
            {([["desktop", "🖥"], ["tablet", "▭"], ["phone", "📱"]] as const).map(([d, icon]) => (
              <button key={d} type="button" title={d} onClick={() => setDevice(d)} className={`rounded px-2 py-1 text-xs ${device === d ? "bg-slate-700 text-white" : "text-slate-400"}`}>
                {icon}
              </button>
            ))}
          </div>
        )}

        <div className="flex items-center gap-0.5">
          <button type="button" className={GHOST} disabled={!past.length} onClick={undo} title="Undo (Ctrl+Z)">
            ↶
          </button>
          <button type="button" className={GHOST} disabled={!future.length} onClick={redo} title="Redo (Ctrl+Shift+Z)">
            ↷
          </button>
          <span className="mx-1 h-5 w-px bg-slate-700" />
          <button type="button" className={GHOST} onClick={() => setDrawer("")}>
            Rules <span className="ml-1 rounded-full bg-slate-800 px-1.5 text-[10px]">{pageRules.length}</span>
          </button>
          {dirty && (
            <button type="button" className={GHOST} onClick={() => open(current)}>
              Discard
            </button>
          )}
          <button type="button" className={PRIMARY} disabled={!dirty || busy} onClick={save} title="Save (Ctrl+S)">
            {dirty ? "Save page" : "Saved"}
          </button>
        </div>
      </div>

      {/* ---------------- AI command line */}
      <div className="border-b border-slate-800 bg-gradient-to-r from-violet-500/[0.06] to-sky-500/[0.06] px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-violet-300">✨</span>
          <input
            value={ai}
            onChange={(e) => setAi(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && askAi()}
            placeholder="Ask AI to change this page - e.g. make GSTIN and PAN mandatory; add Payment terms as a dropdown with values 30, 60 days in Commercials"
            className="flex-1 bg-transparent py-1 text-sm text-slate-100 outline-none placeholder:text-slate-500"
          />
          <button type="button" disabled={!ai.trim() || busy} onClick={askAi} className="rounded-md bg-violet-600 px-3 py-1 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-40">
            {busy ? "Thinking…" : "Preview changes"}
          </button>
        </div>
        {plan && plan.ops.length > 0 && (
          <div className="mt-2 rounded-lg border border-violet-500/30 bg-slate-950/80 p-3">
            <p className="mb-1.5 text-xs text-slate-400">Proposed {plan.engine === "ai" ? "by AI" : "from your words"} - untick anything you don't want:</p>
            <div className="grid gap-1 md:grid-cols-2">
              {plan.preview.map((line, i) => (
                <label key={i} className="flex items-center gap-2 text-sm text-slate-200">
                  <input type="checkbox" className="accent-violet-500" checked={picked[i]} onChange={(e) => setPicked(picked.map((p, j) => (j === i ? e.target.checked : p)))} />
                  {line}
                </label>
              ))}
            </div>
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" className={GHOST} onClick={() => setPlan(null)}>
                Cancel
              </button>
              <button type="button" disabled={busy || !picked.some(Boolean)} onClick={applyPlan} className="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">
                Apply {picked.filter(Boolean).length} change{picked.filter(Boolean).length === 1 ? "" : "s"}
              </button>
            </div>
          </div>
        )}
      </div>

      {mode === "preview" ? (
        /* ---------------- preview */
        <div className="min-h-[640px] bg-[radial-gradient(circle_at_1px_1px,rgb(51_65_85/0.35)_1px,transparent_0)] bg-[length:18px_18px] px-4 py-8">
          <div className={`mx-auto ${width} rounded-2xl border border-slate-700 bg-slate-900 p-6 shadow-2xl transition-all`}>
            <p className="mb-1 text-[11px] uppercase tracking-wider text-slate-500">{entities[current.entity_type]}{current.segment ? ` · ${current.segment}` : ""}</p>
            <h3 className="mb-5 text-lg font-semibold text-slate-100">{current.name}</h3>
            {form && <PageFormView form={form} values={sample} record={null} onChange={(k, v) => setSample((s) => ({ ...s, [k]: v }))} />}
            <p className="mt-4 text-center text-[11px] text-slate-500">Live: mandatory fields and rules are checked as you type, exactly as users will see them.</p>
          </div>
        </div>
      ) : (
        <div className="grid min-h-[640px] grid-cols-[15rem_minmax(0,1fr)_19rem]">
          {/* ---------------- palette */}
          <aside className="flex flex-col border-r border-slate-800 bg-slate-900/40">
            <div className="border-b border-slate-800 p-3">
              <input value={paletteQ} onChange={(e) => setPaletteQ(e.target.value)} placeholder="Search fields…" className={INPUT} />
            </div>
            <div className="flex-1 space-y-4 overflow-y-auto p-3">
              <div>
                <p className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">Elements</p>
                <div className="grid grid-cols-2 gap-1">
                  {([["__button", "▶", "Button", "Runs an intent or a Flow Studio flow"], ["__text", "¶", "Text", "A note, heading or instruction"]] as const).map(
                    ([key, icon, label, hint]) => (
                      <div key={key} draggable onDragStart={() => setDrag({ from: "palette", key })} onDragEnd={() => (setDrag(null), setDropAt(null))}
                        onDoubleClick={() => place(key, sel.kind !== "page" ? sel.s : 0, null)} title={`${hint} - drag onto the page, or double-click`}
                        className="flex cursor-grab items-center gap-1.5 rounded-md border border-violet-500/30 bg-violet-500/[0.06] px-2 py-1.5 text-sm text-violet-200 hover:border-violet-400 active:cursor-grabbing">
                        <span className="inline-flex h-6 w-6 items-center justify-center rounded bg-violet-500/20 font-mono text-[11px]">{icon}</span>
                        {label}
                      </div>
                    )
                  )}
                </div>
              </div>
              {groups.map(([title, list]) =>
                list.length ? (
                  <div key={title}>
                    <p className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                      {title} <span className="text-slate-600">· {list.length}</span>
                    </p>
                    <div className="space-y-1">
                      {list.map((f) => (
                        <div
                          key={f.key}
                          draggable
                          onDragStart={() => setDrag({ from: "palette", key: f.key })}
                          onDragEnd={() => (setDrag(null), setDropAt(null))}
                          onDoubleClick={() => place(f.key, sel.kind !== "page" ? sel.s : 0, null)}
                          title="Drag onto the page, or double-click to add"
                          className="flex cursor-grab items-center gap-2 rounded-md border border-slate-800 bg-slate-900 px-2 py-1.5 text-sm text-slate-300 transition hover:border-slate-600 hover:text-white active:cursor-grabbing"
                        >
                          <TypeIcon type={f.data_type} />
                          <span className="min-w-0 flex-1 truncate">{f.label}</span>
                          {f.segment && <span className="text-[9px] font-semibold text-sky-400">{f.segment}</span>}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null
              )}
              {available.length === 0 && <p className="text-xs text-slate-500">{q ? "No field matches." : "Every field is on the page."}</p>}
            </div>
            <div className="border-t border-slate-800 p-3">
              {newField ? (
                <div className="space-y-2">
                  <input autoFocus value={newField.label} onChange={(e) => setNewField({ ...newField, label: e.target.value })} placeholder="Field name" className={INPUT} />
                  <select value={newField.data_type} onChange={(e) => setNewField({ ...newField, data_type: e.target.value })} className={INPUT}>
                    {Object.entries(TYPE).map(([k, t]) => (
                      <option key={k} value={k}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                  {newField.data_type === "picklist" && <input value={newField.values} onChange={(e) => setNewField({ ...newField, values: e.target.value })} placeholder="Choices, comma-separated" className={INPUT} />}
                  {current.segment && (
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                      <input type="checkbox" className="accent-sky-500" checked={newField.segmentOnly} onChange={(e) => setNewField({ ...newField, segmentOnly: e.target.checked })} />
                      Only for {current.segment}
                    </label>
                  )}
                  <div className="flex justify-end gap-1">
                    <button type="button" className={GHOST} onClick={() => setNewField(null)}>
                      Cancel
                    </button>
                    <button type="button" className={PRIMARY} disabled={!newField.label.trim() || busy || (newField.data_type === "picklist" && !newField.values.trim())} onClick={createField}>
                      Create
                    </button>
                  </div>
                </div>
              ) : (
                <button type="button" onClick={() => setNewField({ label: "", data_type: "text", values: "", segmentOnly: !!current.segment })}
                  className="w-full rounded-md border border-dashed border-slate-700 py-2 text-sm text-slate-400 transition hover:border-sky-500 hover:text-sky-300">
                  + New field
                </button>
              )}
            </div>
          </aside>

          {/* ---------------- canvas */}
          <main className="overflow-y-auto bg-[radial-gradient(circle_at_1px_1px,rgb(51_65_85/0.35)_1px,transparent_0)] bg-[length:18px_18px] p-6" onClick={() => setSel({ kind: "page" })}>
            <div className="mx-auto max-w-4xl space-y-4">
              {layout.sections.map((s, si) => (
                <section
                  key={si}
                  onClick={(e) => (e.stopPropagation(), setSel({ kind: "section", s: si }))}
                  onDragOver={(e) => (e.preventDefault(), dropAt?.s !== si || dropAt.f !== -1 ? setDropAt({ s: si, f: -1 }) : null)}
                  onDrop={(e) => (e.preventDefault(), onDrop(si, null))}
                  className={`rounded-xl border bg-slate-900 shadow-sm transition ${sel.kind === "section" && sel.s === si ? "border-sky-500 ring-2 ring-sky-500/20" : "border-slate-800 hover:border-slate-700"}`}
                >
                  <header className="flex items-center gap-2 border-b border-slate-800 px-4 py-2.5">
                    <h4 className="flex-1 text-xs font-bold uppercase tracking-wider text-slate-300">{s.title}</h4>
                    <span className="text-[11px] text-slate-500">{s.fields.length} field{s.fields.length === 1 ? "" : "s"} · {(s.columns ?? 2) === 2 ? "2 columns" : "1 column"}</span>
                  </header>
                  <div className={`grid gap-x-4 gap-y-3 p-4 ${(s.columns ?? 2) === 2 ? "md:grid-cols-2" : ""}`}>
                    {s.fields.map((f, fi) => {
                      const selected = sel.kind === "field" && sel.s === si && sel.f === fi;
                      const marker = dropAt && dropAt.s === si && dropAt.f === fi;
                      if (isElement(f)) {
                        const runs = runnable.find((r) => r.code === f.intent);
                        return (
                          <div key={f.key || `${f.kind}:${f.id}`} draggable
                            onDragStart={(e) => (e.stopPropagation(), setDrag({ from: "canvas", s: si, f: fi }))}
                            onDragEnd={() => (setDrag(null), setDropAt(null))}
                            onDragOver={(e) => (e.preventDefault(), e.stopPropagation(), !marker && setDropAt({ s: si, f: fi }))}
                            onDrop={(e) => (e.preventDefault(), e.stopPropagation(), onDrop(si, fi))}
                            onClick={(e) => (e.stopPropagation(), setSel({ kind: "field", s: si, f: fi }))}
                            className={`relative cursor-pointer rounded-lg border p-2.5 transition md:col-span-2 ${selected ? "border-violet-500 bg-violet-500/[0.06] ring-2 ring-violet-500/20" : "border-transparent hover:border-slate-700 hover:bg-slate-800/30"}`}>
                            {marker && drag && <span className="absolute -top-2 left-0 right-0 h-0.5 rounded bg-sky-400" />}
                            {f.kind === "button" ? (
                              <div className="flex items-center gap-2">
                                <span className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${f.style === "primary" ? "bg-sky-600 text-white" : f.style === "danger" ? "border border-red-500/50 text-red-300" : "border border-slate-600 text-slate-200"}`}>
                                  {f.label || "Button"}
                                </span>
                                <span className="truncate text-[11px] text-violet-300">
                                  ▶ {runs ? `${runs.flow ? "Flow: " : ""}${runs.label}` : f.intent || "choose what it runs"}
                                </span>
                              </div>
                            ) : (
                              <p className="whitespace-pre-line rounded bg-slate-800/40 px-3 py-2 text-xs text-slate-300">{f.text}</p>
                            )}
                          </div>
                        );
                      }
                      const def = byKey[f.key];
                      if (!def) return null;
                      return (
                        <div
                          key={f.key}
                          draggable
                          onDragStart={(e) => (e.stopPropagation(), setDrag({ from: "canvas", s: si, f: fi }))}
                          onDragEnd={() => (setDrag(null), setDropAt(null))}
                          onDragOver={(e) => (e.preventDefault(), e.stopPropagation(), !marker && setDropAt({ s: si, f: fi }))}
                          onDrop={(e) => (e.preventDefault(), e.stopPropagation(), onDrop(si, fi))}
                          onClick={(e) => (e.stopPropagation(), setSel({ kind: "field", s: si, f: fi }))}
                          className={`group relative cursor-pointer rounded-lg border p-2.5 transition ${
                            (f.span ?? (def.data_type === "textarea" ? "full" : null)) === "full" ? "md:col-span-2" : ""
                          } ${selected ? "border-sky-500 bg-sky-500/[0.06] ring-2 ring-sky-500/20" : "border-transparent hover:border-slate-700 hover:bg-slate-800/30"} ${f.hidden ? "opacity-40" : ""}`}
                        >
                          {marker && drag && <span className="absolute -top-2 left-0 right-0 h-0.5 rounded bg-sky-400" />}
                          <div className="mb-1.5 flex items-center gap-1.5">
                            <span className="text-xs font-medium text-slate-300">{f.label || def.label}</span>
                            {f.mandatory && <span className="text-sky-400">*</span>}
                            {f.readonly && <span className="text-[10px] text-amber-400" title="Read-only">🔒</span>}
                            {f.hidden && <span className="rounded bg-slate-800 px-1 text-[9px] text-slate-400">hidden</span>}
                            {pageRules.some((r) => ruleFields(r).includes(f.key)) && <span className="text-[10px] text-violet-300" title="Has validation rules">ƒ</span>}
                            <span className="ml-auto cursor-grab text-slate-600 opacity-0 transition group-hover:opacity-100" title="Drag to move">⠿</span>
                          </div>
                          <MockControl field={def} />
                        </div>
                      );
                    })}
                    {s.fields.length === 0 && (
                      <div className="rounded-lg border border-dashed border-slate-700 py-6 text-center text-xs text-slate-500 md:col-span-2">Drop fields here</div>
                    )}
                    {dropAt && dropAt.s === si && dropAt.f === -1 && drag && s.fields.length > 0 && <span className="h-0.5 rounded bg-sky-400 md:col-span-2" />}
                  </div>
                </section>
              ))}
              <button type="button" onClick={(e) => (e.stopPropagation(), change((l) => l.sections.push({ title: "New section", columns: 2, fields: [] })), setSel({ kind: "section", s: layout.sections.length }))}
                className="w-full rounded-xl border border-dashed border-slate-700 py-3 text-sm text-slate-400 transition hover:border-sky-500 hover:text-sky-300">
                + Add section
              </button>
            </div>
          </main>

          {/* ---------------- inspector */}
          <aside className="overflow-y-auto border-l border-slate-800 bg-slate-900/40">
            {sel.kind === "field" && selField && isElement(selField) ? (
              <div className="space-y-4 p-4">
                <p className="text-sm font-semibold text-slate-100">{selField.kind === "button" ? "Button" : "Text"}</p>
                {selField.kind === "button" ? (
                  <>
                    <div>
                      <label className={LABEL}>Label</label>
                      <input value={selField.label ?? ""} onChange={(e) => setField({ label: e.target.value })} className={INPUT} />
                    </div>
                    <div>
                      <label className={LABEL}>Runs</label>
                      <select value={selField.intent ?? ""} onChange={(e) => setField({ intent: e.target.value })} className={INPUT}>
                        <option value="">Choose…</option>
                        <optgroup label="Flow Studio flows">
                          {runnable.filter((r) => r.flow).map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
                        </optgroup>
                        <optgroup label="Intents">
                          {runnable.filter((r) => !r.flow).map((r) => <option key={r.code} value={r.code}>{r.label} ({r.code})</option>)}
                        </optgroup>
                      </select>
                      <p className="mt-1 text-[11px] text-slate-500">Opens the intent prefilled with the record on the page - its own checks and permissions apply.</p>
                    </div>
                    <div>
                      <label className={LABEL}>Style</label>
                      <div className="grid grid-cols-3 gap-1">
                        {(["primary", "default", "danger"] as const).map((st) => (
                          <button key={st} type="button" onClick={() => setField({ style: st })}
                            className={`rounded-md border px-2 py-1 text-xs capitalize ${(selField.style ?? "default") === st ? "border-sky-500 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-400"}`}>
                            {st}
                          </button>
                        ))}
                      </div>
                    </div>
                  </>
                ) : (
                  <div>
                    <label className={LABEL}>Text</label>
                    <textarea rows={5} value={selField.text ?? ""} onChange={(e) => setField({ text: e.target.value })} className={`${INPUT} resize-y`} />
                  </div>
                )}
                <button type="button" onClick={() => (change((l) => l.sections[sel.s].fields.splice(sel.f, 1)), setSel({ kind: "section", s: sel.s }))}
                  className="w-full rounded-md border border-slate-700 py-1.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                  Remove
                </button>
              </div>
            ) : sel.kind === "field" && selField && selDef ? (
              <div className="space-y-4 p-4">
                <div className="flex items-start gap-2">
                  <TypeIcon type={selDef.data_type} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-slate-100">{selField.label || selDef.label}</p>
                    <p className="font-mono text-[10px] text-slate-500">
                      {selDef.key} · {TYPE[selDef.data_type]?.label} · {selDef.core ? "core" : selDef.system ? "standard" : "custom"}
                    </p>
                  </div>
                </div>
                <div>
                  <label className={LABEL}>Label on this page</label>
                  <input value={selField.label ?? ""} placeholder={selDef.label} onChange={(e) => setField({ label: e.target.value || undefined })} className={INPUT} />
                </div>
                <div>
                  <label className={LABEL}>Help text</label>
                  <input value={selField.help ?? ""} placeholder={selDef.help || "Shown under the field"} onChange={(e) => setField({ help: e.target.value || undefined })} className={INPUT} />
                </div>
                <div className="space-y-0.5 rounded-lg border border-slate-800 p-1">
                  <Switch on={!!selField.mandatory} onChange={(v) => setField({ mandatory: v })} label="Mandatory" hint="Must be filled in to save" />
                  <Switch on={!!selField.readonly} onChange={(v) => setField({ readonly: v })} label="Read-only" hint="Shown, not editable" />
                  <Switch on={!!selField.hidden} onChange={(v) => setField({ hidden: v })} label="Hidden" hint="Kept on the record, not shown" />
                  {(layout.sections[sel.s].columns ?? 2) === 2 && (
                    <Switch on={(selField.span ?? (selDef.data_type === "textarea" ? "full" : undefined)) === "full"} onChange={(v) => setField({ span: v ? "full" : undefined })} label="Full width" hint="Spans both columns" />
                  )}
                </div>
                {selDef.values.length > 0 && (
                  <div>
                    <label className={LABEL}>Choices</label>
                    <div className="flex flex-wrap gap-1">
                      {selDef.values.map((v) => (
                        <span key={v} className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-300">
                          {v}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <label className={LABEL}>Rules on this field</label>
                  {pageRules.filter((r) => ruleFields(r).includes(selField.key)).map((r) => (
                    <p key={r.id} className="mb-1 rounded bg-slate-800/60 px-2 py-1 text-xs text-slate-300">
                      {r.explanation}
                    </p>
                  ))}
                  <button type="button" onClick={() => setDrawer(`${selField.label || selDef.label} must `)} className="text-xs text-sky-400 hover:text-sky-300">
                    + Add a rule for this field
                  </button>
                </div>
                <div className="flex gap-2 border-t border-slate-800 pt-3">
                  <select value={sel.s} onChange={(e) => {
                    const to = Number(e.target.value);
                    change((l) => {
                      const [item] = l.sections[sel.s].fields.splice(sel.f, 1);
                      l.sections[to].fields.push(item);
                    });
                    setSel({ kind: "field", s: to, f: layout.sections[to].fields.length });
                  }} className={INPUT}>
                    {layout.sections.map((s, i) => (
                      <option key={i} value={i}>
                        Section: {s.title}
                      </option>
                    ))}
                  </select>
                  <button type="button" title="Remove from page (Delete)" onClick={() => (change((l) => l.sections[sel.s].fields.splice(sel.f, 1)), setSel({ kind: "section", s: sel.s }))}
                    className="rounded-md border border-slate-700 px-2.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                    Remove
                  </button>
                </div>
              </div>
            ) : sel.kind === "section" && layout.sections[sel.s] ? (
              <div className="space-y-4 p-4">
                <p className="text-sm font-semibold text-slate-100">Section</p>
                <div>
                  <label className={LABEL}>Title</label>
                  <input value={layout.sections[sel.s].title} onChange={(e) => change((l) => (l.sections[sel.s].title = e.target.value))} className={INPUT} />
                </div>
                <div>
                  <label className={LABEL}>Layout</label>
                  <div className="grid grid-cols-2 gap-2">
                    {[1, 2].map((n) => (
                      <button key={n} type="button" onClick={() => change((l) => (l.sections[sel.s].columns = n))}
                        className={`rounded-md border px-3 py-2 text-xs ${(layout.sections[sel.s].columns ?? 2) === n ? "border-sky-500 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
                        <span className="mb-1 flex gap-1">{Array.from({ length: n }).map((_, i) => <span key={i} className="h-3 flex-1 rounded-sm bg-current opacity-40" />)}</span>
                        {n} column{n > 1 ? "s" : ""}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button type="button" className={GHOST} disabled={sel.s === 0} onClick={() => (change((l) => l.sections.splice(sel.s - 1, 0, ...l.sections.splice(sel.s, 1))), setSel({ kind: "section", s: sel.s - 1 }))}>
                    ↑ Move up
                  </button>
                  <button type="button" className={GHOST} disabled={sel.s === layout.sections.length - 1}
                    onClick={() => (change((l) => l.sections.splice(sel.s + 1, 0, ...l.sections.splice(sel.s, 1))), setSel({ kind: "section", s: sel.s + 1 }))}>
                    ↓ Move down
                  </button>
                </div>
                <button type="button" onClick={() => (change((l) => l.sections.splice(sel.s, 1)), setSel({ kind: "page" }))}
                  className="w-full rounded-md border border-slate-700 py-1.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                  Delete section{layout.sections[sel.s].fields.length ? ` (its ${layout.sections[sel.s].fields.length} fields go back to the palette)` : ""}
                </button>
              </div>
            ) : (
              <div className="space-y-4 p-4">
                <p className="text-sm font-semibold text-slate-100">Page</p>
                <div className="grid grid-cols-3 gap-2 text-center">
                  {stats && [["Fields", stats.fields], ["Mandatory", stats.mandatory], ["Hidden", stats.hidden]].map(([l, n]) => (
                    <div key={l as string} className="rounded-lg border border-slate-800 bg-slate-900 py-2">
                      <p className="text-lg font-semibold text-slate-100">{n}</p>
                      <p className="text-[10px] uppercase tracking-wide text-slate-500">{l}</p>
                    </div>
                  ))}
                </div>
                <dl className="space-y-1.5 text-xs">
                  {[["Record type", entities[current.entity_type]], ["Segment", current.segment ?? "Both"], ["Rules", `${pageRules.length} active`], ["BRT_ID", current.brt_id ?? "—"]].map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <dt className="text-slate-500">{k}</dt>
                      <dd className="text-slate-300">{v}</dd>
                    </div>
                  ))}
                </dl>
                <div className="space-y-0.5 rounded-lg border border-slate-800 p-1">
                  <Switch
                    on={current.is_active}
                    label={current.is_active ? "Active" : "Switched off"}
                    hint={current.is_active ? "Users get this page" : "Nobody gets it until you switch it on"}
                    onChange={(v) =>
                      setPageActive(current.page_key, v)
                        .then((out) => (setPages((ps) => ps.map((p) => (p.page_key === out.page_key ? out : p))), setCurrent(out), notify("ok", v ? "Page switched on" : "Page switched off")))
                        .catch((e) => notify("err", clean(e)))
                    }
                  />
                </div>
                <div>
                  <label className={LABEL}>Given to roles</label>
                  <div className="flex flex-wrap gap-1">
                    {roles.map((r) => {
                      const on = (layout.role_ids ?? []).includes(r.id);
                      return (
                        <button key={r.id} type="button"
                          onClick={() => change((l) => (l.role_ids = on ? (l.role_ids ?? []).filter((x) => x !== r.id) : [...(l.role_ids ?? []), r.id]))}
                          className={`rounded-full border px-2 py-0.5 text-[11px] ${on ? "border-sky-500 bg-sky-500/15 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
                          {r.name}
                        </button>
                      );
                    })}
                    {roles.length === 0 && <span className="text-[11px] text-slate-500">No roles yet (Admin → Roles &amp; Access).</span>}
                  </div>
                  <p className="mt-1 text-[11px] text-slate-500">
                    {(layout.role_ids ?? []).length ? "Only these roles get this page instead of the standard one." : "None picked = everyone gets it."}
                  </p>
                </div>
                <div className="space-y-2">
                  <label className={LABEL}>Page events · start a Flow Studio flow</label>
                  {([["on_open", "When opened"], ["after_save", "After saving"]] as const).map(([event, label]) => (
                    <div key={event} className="flex items-center gap-2">
                      <span className="w-24 shrink-0 text-xs text-slate-400">{label}</span>
                      <select value={layout.events?.[event] ?? ""} className={INPUT}
                        onChange={(e) => change((l) => {
                          const events = { ...(l.events ?? {}) };
                          if (e.target.value) events[event] = e.target.value;
                          else delete events[event];
                          l.events = events;
                        })}>
                        <option value="">Nothing</option>
                        {runnable.filter((r) => r.flow).map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
                      </select>
                    </div>
                  ))}
                  {!runnable.some((r) => r.flow) && <p className="text-[11px] text-slate-500">Make a flow with the "custom intent" trigger in the Flow Studio first.</p>}
                </div>
                {copyName === null ? (
                  <button type="button" onClick={() => setCopyName(`${current.name} (copy)`)} className="w-full rounded-md border border-slate-700 py-1.5 text-xs text-slate-300 hover:border-sky-500 hover:text-sky-300">
                    ⧉ Copy as a new page
                  </button>
                ) : (
                  <div className="flex gap-1">
                    <input autoFocus value={copyName} onChange={(e) => setCopyName(e.target.value)} className={INPUT} />
                    <button type="button" className={PRIMARY} disabled={!copyName.trim() || busy}
                      onClick={() => copyPage(current.page_key, copyName.trim())
                        .then((made) => (setCopyName(null), loadPages(made.page_key), notify("ok", `"${made.name}" created - switched off until you activate it`)))
                        .catch((e) => notify("err", clean(e)))}>
                      Copy
                    </button>
                  </div>
                )}
                <p className="rounded-lg bg-slate-800/40 p-3 text-xs leading-relaxed text-slate-400">
                  Drag fields, buttons and text from the left onto a section, click one to change it, or ask the AI above - e.g. “add a button
                  Send welcome kit that runs the flow Welcome kit; give it to the Enterprise Sales role; activate”. <b>Ctrl+Z</b> undoes, <b>Ctrl+S</b> saves.
                  Everything here is this tenant's own - the standard pages other tenants use are untouched.
                </p>
                {current.customized && !dirty && (
                  <button type="button"
                    onClick={() => window.confirm(current.is_default ? "Go back to the standard layout?" : "Delete this page?") &&
                      resetPage(current.page_key).then(() => (loadPages(current.is_default ? current.page_key : undefined), notify("ok", current.is_default ? "Back to the standard layout" : "Page deleted")))}
                    className="w-full rounded-md border border-slate-700 py-1.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                    {current.is_default ? "Reset to the standard layout" : "Delete this page"}
                  </button>
                )}
              </div>
            )}
          </aside>
        </div>
      )}

      {/* ---------------- new page dialog */}
      {newPage && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 p-4" onClick={() => setNewPage(null)}>
          <div className="w-full max-w-lg space-y-3 rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-slate-100">New page</h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={LABEL}>Record type</label>
                <select value={newPage.entity_type} onChange={(e) => setNewPage({ ...newPage, entity_type: e.target.value })} className={INPUT}>
                  {Object.entries(entities).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className={LABEL}>Segment</label>
                <select value={newPage.segment} onChange={(e) => setNewPage({ ...newPage, segment: e.target.value })} className={INPUT}>
                  <option value="">Both</option>
                  {segments.map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className={LABEL}>Name</label>
              <input autoFocus value={newPage.name} onChange={(e) => setNewPage({ ...newPage, name: e.target.value })} placeholder="e.g. Enterprise onboarding" className={INPUT} />
            </div>
            <div>
              <label className={LABEL}>✨ What should it ask for? (optional)</label>
              <textarea rows={3} value={newPage.instruction} onChange={(e) => setNewPage({ ...newPage, instruction: e.target.value })}
                placeholder="e.g. make GSTIN and PAN mandatory; add Payment terms as a dropdown with values 30 days, 60 days; hide Gender" className={`${INPUT} resize-none`} />
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" className={GHOST} onClick={() => setNewPage(null)}>
                Cancel
              </button>
              <button type="button" className={PRIMARY} disabled={!newPage.name.trim() || busy} onClick={makePage}>
                Create page
              </button>
            </div>
          </div>
        </div>
      )}

      {drawer !== null && (
        <RulesDrawer page={current} rules={rules} flows={flows} sample={sample} initial={drawer} onClose={() => setDrawer(null)} reload={loadRules} />
      )}

      {toast && (
        <div className={`fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg px-4 py-2 text-sm shadow-2xl ${toast.tone === "ok" ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}`}>
          {toast.text}
        </div>
      )}
    </div>
  );
}
