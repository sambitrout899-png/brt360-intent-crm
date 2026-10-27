import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { c360Agent, c360Preview, c360Restore, c360Save, c360Studio, c360Undo, listParties } from "../../api/client";
import type { C360ConnectorDraft, C360Layout, C360Pane, C360Plan, C360Studio, C360View, C360Widget, C360WidgetType, C360Width } from "../../api/c360Types";
import type { Party } from "../../api/types";
import { notifyIntentExecuted } from "../../workspace";
import AccountWorkspace from "./AccountWorkspace";
import AudienceEditor from "./AudienceEditor";
import { PAGE_TABS, STANDARD_COMPONENTS, audienceLabel, paneTab, sectionState, tabOfDomain, workspaceFrame, type PageTabId } from "./workspacePresentation";
import LookTab from "./LookTab";
import SourcesTab from "./SourcesTab";
import { compatibleTypes, newId, sameLayout, TYPE_ICONS, TYPE_LABELS, WIDTH_LABELS } from "./studioUtil";

// ------------------------------------------------------------------ the Customer 360 page's tabs (one definition with the live page)

type PageTab = PageTabId;
const tabLabel = (id: string) => PAGE_TABS.find((t) => t.id === id)?.label ?? id;
const SECTION_NOTE: Record<string, string> = {
  shown: "Shown",
  secondary: "Collapsed below your panes - a configured list on this tab covers it",
  replaced: "Replaced by a configured list on this tab",
};

// ------------------------------------------------------------------ look & feel (same as the Page Designer)

const INPUT = "w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100 outline-none transition focus:border-sky-500 focus:ring-1 focus:ring-sky-500/40";
const LABEL = "mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500";
const GHOST = "rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-300 transition hover:bg-slate-800 hover:text-white disabled:opacity-30 disabled:hover:bg-transparent";
const PRIMARY = "rounded-md bg-sky-600 px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-sky-500 disabled:opacity-40";
const SPAN: Record<C360Width, string> = { third: "col-span-2", half: "col-span-3", two_thirds: "col-span-4", full: "col-span-6" };
const WIDE: C360WidgetType[] = ["table", "records", "insights"];
const SWATCHES = ["#38bdf8", "#a78bfa", "#34d399", "#f59e0b", "#f472b6", "#f87171", "#60a5fa", "#94a3b8"];
const STARTERS = [
  "Put sales, service, network and billing side by side with AI insights on top",
  "Connect all the respective systems",
  "Make it compact with our brand colour",
  "Show tickets by priority as a bar chart",
];

type Selection = { kind: "page" } | { kind: "pane"; id: string } | { kind: "widget"; pane: string; id: string };
type Drag = { from: "recipe"; recipe: string } | { from: "widget"; pane: string; id: string } | { from: "pane"; id: string };
type Message = { role: "you" | "agent"; text: string; plan?: C360Plan };
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

function Drawer({ title, sub, onClose, children }: { title: string; sub: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-[70] flex justify-end bg-slate-950/60" onClick={onClose}>
      <aside className="flex h-full w-full max-w-md flex-col border-l border-slate-800 bg-slate-900 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <header className="flex items-center gap-3 border-b border-slate-800 px-5 py-4">
          <div className="flex-1">
            <h3 className="text-base font-semibold text-slate-100">{title}</h3>
            <p className="text-xs text-slate-500">{sub}</p>
          </div>
          <button type="button" onClick={onClose} className={GHOST}>
            ✕
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>
  );
}

/** Configure 360 (INT-33): design the Customer 360 page - the same tabs the live
 * page has - by hand or by asking the AI, see a real customer's page, publish. */
export default function Configure360Studio({
  initialInstruction,
  initialPartyId,
  initialTab,
  onClose,
}: {
  initialInstruction?: string;
  initialPartyId?: number;
  initialTab?: "ai" | "layout" | "look" | "sources";
  onClose: () => void;
}) {
  const [studio, setStudio] = useState<C360Studio | null>(null);
  const [draft, setDraft] = useState<C360Layout | null>(null);
  const [past, setPast] = useState<C360Layout[]>([]);
  const [future, setFuture] = useState<C360Layout[]>([]);
  const [pending, setPending] = useState<C360ConnectorDraft[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [partyId, setPartyId] = useState<number | null>(initialPartyId ?? null);
  const [preview, setPreview] = useState<C360View | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [pageTab, setPageTab] = useState<PageTab>("overview");
  const [mode, setMode] = useState<"design" | "preview">("design");
  const [device, setDevice] = useState<"desktop" | "tablet" | "phone">("desktop");
  const [sel, setSel] = useState<Selection>({ kind: "page" });
  const [audienceComponent, setAudienceComponent] = useState("service_accounts");
  const [drag, setDrag] = useState<Drag | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [ai, setAi] = useState("");
  const [thinking, setThinking] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [drawer, setDrawer] = useState<"look" | "sources" | "history" | "chat" | null>(initialTab === "look" ? "look" : initialTab === "sources" ? "sources" : null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ tone: "ok" | "err"; text: string } | null>(null);
  const changeLog = useRef<string[]>([]);
  const sentInitial = useRef(false);
  const toastTimer = useRef<number | undefined>(undefined);

  const notify = useCallback((tone: "ok" | "err", text: string) => {
    setToast({ tone, text });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), tone === "ok" ? 3500 : 6000);
  }, []);

  const reload = useCallback(
    (resetDraft: boolean) =>
      c360Studio()
        .then((s) => {
          setStudio(s);
          if (resetDraft) {
            setDraft(s.layout);
            setPast([]);
            setFuture([]);
          }
        })
        .catch((e) => notify("err", e instanceof Error ? e.message : "Could not open the studio")),
    [notify]
  );

  useEffect(() => {
    reload(true);
    listParties().then((list) => {
      setParties(list);
      setPartyId((id) => id ?? list[0]?.id ?? null);
    });
  }, [reload]);

  // a real customer's page for the unsaved draft
  const draftKey = JSON.stringify({ draft, pending, partyId });
  useEffect(() => {
    if (!draft || partyId === null) return;
    let cancelled = false;
    const handle = setTimeout(() => {
      setPreviewBusy(true);
      c360Preview(partyId, draft, pending)
        .then(value => { if (!cancelled) setPreview(value); })
        .catch(() => null)
        .finally(() => { if (!cancelled) setPreviewBusy(false); });
    }, 350);
    return () => { cancelled = true; clearTimeout(handle); };
    // draftKey captures draft, pending and partyId
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey]);

  const change = useCallback((fn: (l: C360Layout) => void) => {
    if (!draft) return;
    const next = clone(draft);
    fn(next);
    setPast((p) => [...p.slice(-49), draft]);
    setFuture([]);
    setDraft(next);
  }, [draft]);
  const undo = useCallback(() => {
    if (!past.length || !draft) return;
    setFuture((f) => [draft, ...f]);
    setDraft(past[past.length - 1]);
    setPast(past.slice(0, -1));
  }, [past, draft]);
  const redo = useCallback(() => {
    if (!future.length || !draft) return;
    setPast((p) => [...p, draft]);
    setDraft(future[0]);
    setFuture(future.slice(1));
  }, [future, draft]);

  const ask = useCallback(async (instruction: string) => {
    const text = instruction.trim();
    if (!text || !draft) return;
    setMessages((m) => [...m, { role: "you", text }]);
    setAi("");
    setThinking(true);
    try {
      const plan = await c360Agent(text, draft);
      setMessages((m) => [...m, { role: "agent", text: plan.reply, plan }]);
      if (plan.understood) {
        setPast((p) => [...p.slice(-49), draft]);
        setFuture([]);
        setDraft(plan.layout);
        changeLog.current.push(...plan.changes);
        if (plan.connectors.length) setPending((p) => [...p.filter((x) => !plan.connectors.some((c) => c.key === x.key)), ...plan.connectors]);
        notify("ok", plan.changes.slice(0, 2).join(" · ") || "Updated");
      } else notify("err", plan.reply);
    } catch (e) {
      notify("err", e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setThinking(false);
    }
  }, [draft, notify]);

  useEffect(() => {
    if (initialInstruction && draft && !sentInitial.current) {
      sentInitial.current = true;
      ask(initialInstruction);
    }
  }, [initialInstruction, draft, ask]);

  const dirty = !!studio && !!draft && (!sameLayout(studio.layout, draft) || pending.length > 0);

  const save = useCallback(async () => {
    if (!draft || !dirty) return;
    setSaving(true);
    try {
      const summary = changeLog.current.length ? changeLog.current.join("; ") : "Edited in the Configure 360 studio";
      const res = await c360Save(draft, summary, pending);
      changeLog.current = [];
      setPending([]);
      await reload(true);
      notifyIntentExecuted();
      notify("ok", `Published version ${res.version}${res.connectors_created.length ? ` · connected ${res.connectors_created.join(", ")}` : ""}`);
    } catch (e) {
      notify("err", e instanceof Error ? e.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }, [draft, dirty, pending, reload, notify]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest("input, textarea, select");
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      } else if (!typing && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (!typing && (e.key === "Delete" || e.key === "Backspace") && sel.kind !== "page") {
        e.preventDefault();
        change((l) => {
          if (sel.kind === "pane") l.panes = l.panes.filter((p) => p.id !== sel.id);
          else if (sel.kind === "widget") l.panes = l.panes.map((p) => (p.id === sel.pane ? { ...p, widgets: p.widgets.filter((w) => w.id !== sel.id) } : p));
        });
        setSel({ kind: "page" });
      } else if (e.key === "Escape") setSel({ kind: "page" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save, undo, redo, sel, change]);

  const close = () => {
    if (dirty && !window.confirm("You have unpublished changes. Close without publishing?")) return;
    onClose();
  };

  // ---------------- editing helpers
  const cat = studio?.catalog;
  const domainMeta = (key: string) => cat?.domains.find((d) => d.key === key);
  const tabPanes = useMemo(() => (draft ? draft.panes.filter((p) => paneTab(p) === pageTab) : []), [draft, pageTab]);
  const counts = useMemo(() => Object.fromEntries(PAGE_TABS.map((t) => [t.id, draft?.panes.filter((p) => paneTab(p) === t.id).length ?? 0])), [draft]);
  const tabDef = PAGE_TABS.find((t) => t.id === pageTab) ?? PAGE_TABS[0];
  const selPane = draft && sel.kind !== "page" ? draft.panes.find((p) => p.id === (sel.kind === "pane" ? sel.id : sel.pane)) ?? null : null;
  const selWidget = selPane && sel.kind === "widget" ? selPane.widgets.find((w) => w.id === sel.id) ?? null : null;

  const addPane = (domain?: string) => {
    const d = domainMeta(domain ?? tabDef.domains[0] ?? "custom");
    if (!d) return;
    // A pane is placed on the tab being designed, whatever its data area.
    const tab = tabOfDomain(d.key) === pageTab ? null : pageTab;
    const pane: C360Pane = { id: newId("p"), title: d.label === "Overview" ? "New pane" : d.label, domain: d.key, icon: d.icon, color: d.color, width: "half", collapsed: false, tab, widgets: [] };
    change((l) => l.panes.push(pane));
    setSel({ kind: "pane", id: pane.id });
    return pane.id;
  };
  const addWidget = (paneId: string, recipe: string, before: string | null = null) => {
    const base = studio?.recipe_widgets[recipe];
    if (!base) return;
    const w: C360Widget = { ...clone(base), id: newId("w") };
    change((l) => {
      const pane = l.panes.find((p) => p.id === paneId);
      if (!pane) return;
      const at = before ? pane.widgets.findIndex((x) => x.id === before) : -1;
      pane.widgets.splice(at < 0 ? pane.widgets.length : at, 0, w);
    });
    setSel({ kind: "widget", pane: paneId, id: w.id });
  };
  const updatePane = (id: string, patch: Partial<C360Pane>) => change((l) => (l.panes = l.panes.map((p) => (p.id === id ? { ...p, ...patch } : p))));
  const updateWidget = (paneId: string, id: string, patch: Partial<C360Widget>) =>
    change((l) => (l.panes = l.panes.map((p) => (p.id === paneId ? { ...p, widgets: p.widgets.map((w) => (w.id === id ? { ...w, ...patch } : w)) } : p))));

  const dropOnPane = (paneId: string, before: string | null) => {
    if (!drag) return;
    if (drag.from === "recipe") addWidget(paneId, drag.recipe, before);
    else if (drag.from === "widget") {
      change((l) => {
        const src = l.panes.find((p) => p.id === drag.pane);
        const dst = l.panes.find((p) => p.id === paneId);
        if (!src || !dst) return;
        const i = src.widgets.findIndex((w) => w.id === drag.id);
        if (i < 0) return;
        const [w] = src.widgets.splice(i, 1);
        const at = before ? dst.widgets.findIndex((x) => x.id === before) : -1;
        dst.widgets.splice(at < 0 ? dst.widgets.length : at, 0, w);
      });
      setSel({ kind: "widget", pane: paneId, id: drag.id });
    } else if (drag.from === "pane" && drag.id !== paneId) {
      change((l) => {
        const i = l.panes.findIndex((p) => p.id === drag.id);
        const [p] = l.panes.splice(i, 1);
        l.panes.splice(l.panes.findIndex((x) => x.id === paneId), 0, p);
      });
    }
    setDrag(null);
    setDropTarget(null);
  };

  function sourcesFor(dataset: string | null): { value: string; label: string }[] {
    if (!dataset || !studio) return [];
    const ds = studio.catalog.datasets.find((d) => d.key === dataset);
    const out = [{ value: "", label: ds?.internal ? "Automatic (BRT 360)" : ds?.inventory ? "Automatic (a connected system, else BRT 360 Network Inventory)" : "Automatic (first connected system)" }];
    if (ds?.internal) out.push({ value: "brt", label: "BRT 360" });
    else if (ds?.inventory) out.push({ value: "brt", label: "BRT 360 Network Inventory" });
    for (const c of studio.connectors) if (c.endpoints[dataset]) out.push({ value: c.key, label: `${c.name}${c.is_active ? "" : " (off)"}` });
    for (const d of pending) if (d.endpoints[dataset]) out.push({ value: d.key, label: `${d.name} (on publish)` });
    return out;
  }

  const recipes = (cat?.recipes ?? []).filter((r) => {
    const t = q.trim().toLowerCase();
    return t ? `${r.title} ${r.description} ${r.domain}`.toLowerCase().includes(t) : true;
  });
  const recipeGroups = (cat?.domains ?? []).map((d) => [d, recipes.filter((r) => r.domain === d.key)] as const).filter(([, list]) => list.length);
  const width = { desktop: "max-w-6xl", tablet: "max-w-[768px]", phone: "max-w-[390px]" }[device];
  const kpiSet = new Set(draft?.header.kpis ?? []);
  const recipeTitle = (id: string) => cat?.recipes.find((r) => r.id === id)?.title ?? id;

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-slate-950">
      {/* ---------------- toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 bg-slate-900/80 px-3 py-2">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-violet-600 to-sky-600 text-base">🧩</span>
        <div className="mr-1">
          <p className="text-sm font-semibold text-slate-100">Configure 360</p>
          <p className="text-[11px] text-slate-500">
            Live v{studio?.version ?? "…"}
            {studio?.version === 0 && " (built-in)"}
            {dirty && <span className="ml-1.5 text-amber-400">● unpublished changes</span>}
          </p>
        </div>
        <select value={partyId ?? ""} onChange={(e) => setPartyId(Number(e.target.value))} title="Preview with this customer's data"
          className="max-w-[180px] rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-200">
          {parties.map((p) => (
            <option key={p.id} value={p.id}>
              👤 {p.name}
            </option>
          ))}
        </select>

        <div className="mx-auto flex max-w-full items-center overflow-x-auto rounded-md border border-slate-700 bg-slate-950 p-0.5">
          {PAGE_TABS.map((t) => (
            <button key={t.id} type="button" onClick={() => (setPageTab(t.id), setSel({ kind: "page" }))}
              className={`shrink-0 whitespace-nowrap rounded px-2.5 py-1 text-xs font-semibold ${pageTab === t.id ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"}`}>
              {t.label}
              {counts[t.id] > 0 && <span className="ml-1 text-[10px] text-slate-400">{counts[t.id]}</span>}
            </button>
          ))}
        </div>

        <div className="flex items-center rounded-md border border-slate-700 bg-slate-950 p-0.5">
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
          <button type="button" className={GHOST} onClick={() => setDrawer("look")}>
            🎨 Look
          </button>
          <button type="button" className={GHOST} onClick={() => setDrawer("sources")}>
            🔌 Data sources{pending.length > 0 && <span className="ml-1 rounded-full bg-amber-500/20 px-1.5 text-[10px] text-amber-300">{pending.length}</span>}
          </button>
          <button type="button" className={GHOST} onClick={() => setDrawer("history")}>
            🕘 History
          </button>
          {dirty && (
            <button type="button" className={GHOST} onClick={() => studio && (setDraft(studio.layout), setPending([]), (changeLog.current = []), setPast([]), setFuture([]))}>
              Discard
            </button>
          )}
          <button type="button" className={PRIMARY} disabled={!dirty || saving} onClick={save} title="Ctrl+S">
            {saving ? "Publishing…" : dirty ? "Publish" : "Published"}
          </button>
          <button type="button" className={GHOST} onClick={close} title="Close">
            ✕
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
            onKeyDown={(e) => e.key === "Enter" && ask(ai)}
            placeholder="Ask AI - e.g. add a network pane with latency and connect our OSS at https://oss.example/api · make it compact with our brand colour"
            className="flex-1 bg-transparent py-1 text-sm text-slate-100 outline-none placeholder:text-slate-500"
          />
          {messages.length > 0 && (
            <button type="button" className={GHOST} onClick={() => setDrawer("chat")}>
              Conversation · {messages.filter((m) => m.role === "you").length}
            </button>
          )}
          <button type="button" disabled={!ai.trim() || thinking} onClick={() => ask(ai)} className="rounded-md bg-violet-600 px-3 py-1 text-xs font-semibold text-white hover:bg-violet-500 disabled:opacity-40">
            {thinking ? "Working…" : "Apply"}
          </button>
        </div>
        {messages.length === 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1 pl-6">
            {(studio?.suggestions.length ? studio.suggestions : STARTERS).slice(0, 4).map((s) => (
              <button key={s} type="button" onClick={() => ask(s)} className="rounded-full bg-slate-800/80 px-2.5 py-0.5 text-[11px] text-slate-400 hover:text-sky-300">
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      {!studio || !draft || !cat ? (
        <p className="p-8 text-sm text-slate-500">Opening the studio…</p>
      ) : mode === "preview" ? (
        /* ---------------- preview: the real page for a real customer */
        <div className="min-h-0 flex-1 overflow-y-auto bg-[radial-gradient(circle_at_1px_1px,rgb(51_65_85/0.35)_1px,transparent_0)] bg-[length:18px_18px] px-4 py-6">
          <div className={`mx-auto ${width} transition-all`}>
            <p className="mb-2 flex items-center gap-2 text-xs text-slate-500">
              <span className={`h-2 w-2 rounded-full ${previewBusy ? "animate-pulse bg-amber-400" : "bg-emerald-400"}`} />
              {preview ? `${preview.party.name} · ${preview.audience?.customer_type ?? "customer"} · ${preview.audience?.segment ?? "segment not recorded"} · unpublished draft, exactly as the Customer 360 page renders it. Actions are disabled in preview.` : "Loading…"}
            </p>
            {preview && partyId !== null && preview.party.id === partyId ? (
              <div className="account-workspace" {...workspaceFrame(preview)}>
                <AccountWorkspace key={partyId} partyId={partyId} view={preview} preview tab={pageTab} onTabChange={(t) => setPageTab(t)}
                  onRequestIntent={() => undefined} onConnect={() => setDrawer("sources")} />
              </div>
            ) : (
              <div className="h-96 animate-pulse rounded-xl border border-slate-800 bg-slate-900/50" />
            )}
          </div>
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[16rem_minmax(0,1fr)_20rem]">
          {/* ---------------- palette: charts & tables */}
          <aside className="flex min-h-0 flex-col border-r border-slate-800 bg-slate-900/40">
            <div className="border-b border-slate-800 p-3">
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search charts - revenue, tickets…" className={INPUT} />
            </div>
            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-3">
              {recipeGroups.map(([d, list]) => (
                <div key={d.key}>
                  <p className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    {d.icon} {d.label}
                  </p>
                  <div className="space-y-1">
                    {list.map((r) => (
                      <div
                        key={r.id}
                        draggable
                        onDragStart={() => setDrag({ from: "recipe", recipe: r.id })}
                        onDragEnd={() => (setDrag(null), setDropTarget(null))}
                        onDoubleClick={() => {
                          const target = selPane && paneTab(selPane) === pageTab ? selPane.id : tabPanes[0]?.id ?? addPane(r.domain);
                          if (target) addWidget(target, r.id);
                        }}
                        title={`${r.description} - drag onto a pane, or double-click to add`}
                        className="flex cursor-grab items-start gap-2 rounded-md border border-slate-800 bg-slate-900 px-2 py-1.5 transition hover:border-slate-600 active:cursor-grabbing"
                      >
                        <span className="mt-0.5 text-sm">{TYPE_ICONS[r.type]}</span>
                        <span className="min-w-0">
                          <span className="block truncate text-xs font-medium text-slate-200">{r.title}</span>
                          <span className="block truncate text-[10px] text-slate-500">{TYPE_LABELS[r.type]} · {r.description}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {recipeGroups.length === 0 && <p className="text-xs text-slate-500">No chart matches.</p>}
            </div>
          </aside>

          {/* ---------------- canvas: this tab of the page */}
          <main className="min-h-0 overflow-y-auto bg-[radial-gradient(circle_at_1px_1px,rgb(51_65_85/0.35)_1px,transparent_0)] bg-[length:18px_18px] p-6" onClick={() => setSel({ kind: "page" })}>
            <div className="mx-auto max-w-5xl space-y-4">
              <button type="button" onClick={(e) => (e.stopPropagation(), setSel({ kind: "page" }))}
                className="w-full rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-left hover:border-slate-700">
                <div className="flex items-center gap-2">
                  <p className="flex-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Customer header and tabs · every tab</p>
                  {draft.header.show_health && <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-300">Health score</span>}
                </div>
                <p className="mt-1 text-xs text-slate-400">Name with status, segment and KYC · account number, industry, customer since and owner · contact details and the customer's next actions · tabs with their record counts.</p>
                {pageTab === "overview" && (
                  <div className="mt-3 border-t border-slate-800 pt-3">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Needs attention · top of this tab</p>
                    <p className="mt-1 text-xs text-slate-400">
                      {draft.panes.some((p) => paneTab(p) === "overview" && p.widgets.some((w) => w.type === "insights"))
                        ? "Shown in your Account alerts widget instead."
                        : <>Up to three risks and next steps from the account insights · Visible to: <span className="text-sky-400">{audienceLabel(draft.component_audiences?.insights)}</span></>}
                    </p>
                  </div>
                )}
                {pageTab === "overview" && (
                  <div className="mt-3 border-t border-slate-800 pt-3">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Headline numbers · below the alerts, this tab only</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {draft.header.kpis.map((k) => <span key={k} className="rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-slate-300">{recipeTitle(k)}</span>)}
                      {draft.header.kpis.length === 0 && <span className="text-xs text-slate-500">None chosen - pick them in the Page inspector.</span>}
                    </div>
                  </div>
                )}
              </button>
              {tabPanes.length > 0 && <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Your panes</p>}

              <div className="grid grid-cols-6 gap-4">
                {tabPanes.map((pane) => {
                  const selected = sel.kind === "pane" && sel.id === pane.id;
                  return (
                    <section
                      key={pane.id}
                      draggable
                      onDragStart={(e) => (e.stopPropagation(), setDrag({ from: "pane", id: pane.id }))}
                      onDragEnd={() => (setDrag(null), setDropTarget(null))}
                      onDragOver={(e) => (e.preventDefault(), dropTarget !== pane.id && setDropTarget(pane.id))}
                      onDrop={(e) => (e.preventDefault(), dropOnPane(pane.id, null))}
                      onClick={(e) => (e.stopPropagation(), setSel({ kind: "pane", id: pane.id }))}
                      className={`${SPAN[pane.width] ?? "col-span-3"} rounded-xl border bg-slate-900 shadow-sm transition ${
                        selected ? "border-sky-500 ring-2 ring-sky-500/20" : dropTarget === pane.id && drag ? "border-sky-400/70" : "border-slate-800 hover:border-slate-700"
                      }`}
                    >
                      <header className="flex items-center gap-2 border-b border-slate-800 px-3 py-2">
                        <span className="flex h-7 w-7 items-center justify-center rounded-md text-sm" style={{ background: `${pane.color}22` }}>
                          {pane.icon}
                        </span>
                        <h4 className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-100">{pane.title}</h4>
                        <span title={audienceLabel(pane.audience)} className="max-w-36 truncate text-[10px] text-sky-400">{audienceLabel(pane.audience)}</span>
                        <span className="text-[10px] text-slate-500">{WIDTH_LABELS[pane.width]}</span>
                        <span className="h-1.5 w-8 rounded-full" style={{ background: `linear-gradient(90deg, ${pane.color}, transparent)` }} />
                      </header>
                      <div className="grid grid-cols-2 gap-2 p-3">
                        {pane.widgets.map((w) => {
                          const wsel = sel.kind === "widget" && sel.id === w.id;
                          const meta = cat.datasets.find((d) => d.key === w.dataset);
                          return (
                            <div
                              key={w.id}
                              draggable
                              onDragStart={(e) => (e.stopPropagation(), setDrag({ from: "widget", pane: pane.id, id: w.id }))}
                              onDragEnd={() => (setDrag(null), setDropTarget(null))}
                              onDragOver={(e) => (e.preventDefault(), e.stopPropagation(), dropTarget !== w.id && setDropTarget(w.id))}
                              onDrop={(e) => (e.preventDefault(), e.stopPropagation(), dropOnPane(pane.id, w.id))}
                              onClick={(e) => (e.stopPropagation(), setSel({ kind: "widget", pane: pane.id, id: w.id }))}
                              className={`group relative cursor-pointer rounded-lg border p-2.5 transition ${WIDE.includes(w.type) || w.span === 2 || pane.width === "third" ? "col-span-2" : ""} ${
                                wsel ? "border-sky-500 bg-sky-500/[0.06] ring-2 ring-sky-500/20" : "border-slate-800 bg-slate-950/60 hover:border-slate-600"
                              }`}
                            >
                              {dropTarget === w.id && drag && drag.from !== "pane" && <span className="absolute -top-1.5 left-0 right-0 h-0.5 rounded bg-sky-400" />}
                              <div className="flex items-center gap-1.5">
                                <span className="text-sm">{TYPE_ICONS[w.type]}</span>
                                <span className="min-w-0 flex-1 truncate text-xs font-semibold text-slate-200">{w.title}</span>
                                <span className="cursor-grab text-slate-600 opacity-0 group-hover:opacity-100">⠿</span>
                              </div>
                              <p className="mt-1 text-[10px] text-sky-400">{audienceLabel(w.audience)}</p>
                              <div className="mt-2 flex h-10 items-end gap-0.5 opacity-40">
                                {w.type === "kpi" || w.type === "gauge" ? (
                                  <span className="text-lg font-bold text-slate-300">123</span>
                                ) : (
                                  [4, 7, 5, 9, 6, 8].map((h, i) => <span key={i} className="flex-1 rounded-sm" style={{ height: `${h * 10}%`, background: pane.color }} />)
                                )}
                              </div>
                              <p className="mt-1.5 truncate text-[10px] text-slate-500">
                                {TYPE_LABELS[w.type]}
                                {meta ? ` · ${meta.label}` : ""}
                                {w.source ? ` · from ${studio.connectors.find((c) => c.key === w.source)?.name ?? (w.source === "brt" ? "BRT 360" : w.source)}` : ""}
                              </p>
                            </div>
                          );
                        })}
                        {pane.widgets.length === 0 && (
                          <div className="col-span-2 rounded-lg border border-dashed border-slate-700 py-6 text-center text-xs text-slate-500">
                            Drop a chart or table here
                            <span className="mt-1 block text-[11px] text-amber-300/80">An empty pane is not shown on the page.</span>
                          </div>
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
              <button type="button" onClick={(e) => (e.stopPropagation(), addPane())}
                className="w-full rounded-xl border border-dashed border-slate-700 py-3 text-sm text-slate-400 transition hover:border-sky-500 hover:text-sky-300">
                + Add a pane to {tabDef.label}
              </button>

              <div>
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Standard sections · below your panes</p>
                <div className="space-y-2">
                  {tabDef.builtIn.map((key) => {
                    const state = sectionState(pageTab, key, draft.panes);
                    const audience = draft.component_audiences?.[key];
                    return (
                      <div key={key} className={`flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3 ${state === "replaced" ? "opacity-50" : ""}`}>
                        <span className="text-slate-500" aria-hidden="true">🔒</span>
                        <div className="min-w-0 flex-1">
                          <p className={`text-sm font-medium text-slate-200 ${state === "replaced" ? "line-through" : ""}`}>{STANDARD_COMPONENTS[key]}</p>
                          <p className="text-[11px] text-slate-500">{SECTION_NOTE[state]} · Visible to: <span className="text-sky-400">{audienceLabel(audience)}</span></p>
                        </div>
                        <button type="button" onClick={(e) => (e.stopPropagation(), setAudienceComponent(key), setSel({ kind: "page" }))} className={GHOST}>
                          Who sees it
                        </button>
                      </div>
                    );
                  })}
                </div>
                <p className="mt-2 text-[11px] text-slate-500">Built into every account and always kept up to date. A record list or table you add on this tab replaces the matching standard list.</p>
              </div>
            </div>
          </main>

          {/* ---------------- inspector */}
          <aside className="min-h-0 overflow-y-auto border-l border-slate-800 bg-slate-900/40">
            {sel.kind === "widget" && selPane && selWidget ? (
              <div className="space-y-4 p-4">
                <div className="flex items-start gap-2">
                  <span className="text-lg">{TYPE_ICONS[selWidget.type]}</span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-100">{selWidget.title}</p>
                    <p className="text-[11px] text-slate-500">in {selPane.title}</p>
                  </div>
                </div>
                <div>
                  <label className={LABEL}>Title</label>
                  <input value={selWidget.title} onChange={(e) => updateWidget(selPane.id, selWidget.id, { title: e.target.value.slice(0, 80) })} className={INPUT} />
                </div>
                {compatibleTypes(selWidget).length > 1 && (
                  <div>
                    <label className={LABEL}>Show as</label>
                    <div className="grid grid-cols-3 gap-1.5">
                      {compatibleTypes(selWidget).map((t) => (
                        <button key={t} type="button" onClick={() => updateWidget(selPane.id, selWidget.id, { type: t })}
                          className={`rounded-md border px-2 py-2 text-xs ${selWidget.type === t ? "border-sky-500 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
                          <span className="block text-base">{TYPE_ICONS[t]}</span>
                          {TYPE_LABELS[t]}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <AudienceEditor value={selWidget.audience} inherited onChange={audience => updateWidget(selPane.id, selWidget.id, { audience })} />
                {selWidget.type === "records" && (
                  <div>
                    <label className={LABEL}>Sections shown</label>
                    <div className="flex flex-wrap gap-1">
                      {cat.record_sections.map((s) => {
                        const sections = (selWidget.metric.sections as string[]) ?? [];
                        const on = sections.includes(s);
                        return (
                          <button key={s} type="button"
                            onClick={() => updateWidget(selPane.id, selWidget.id, { metric: { ...selWidget.metric, sections: on ? sections.filter((x) => x !== s) : [...sections, s] } })}
                            className={`rounded-full px-2.5 py-0.5 text-[11px] capitalize ${on ? "bg-violet-600 text-white" : "bg-slate-800 text-slate-400"}`}>
                            {s.replace("_", " ")}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                {sourcesFor(selWidget.dataset).length > 0 && (
                  <div>
                    <label className={LABEL}>{cat.datasets.find((d) => d.key === selWidget.dataset)?.label ?? "Data"} comes from</label>
                    <select value={selWidget.source ?? ""} onChange={(e) => updateWidget(selPane.id, selWidget.id, { source: e.target.value || null })} className={INPUT}>
                      {sourcesFor(selWidget.dataset).map((s) => (
                        <option key={s.value} value={s.value}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                    <button type="button" onClick={() => setDrawer("sources")} className="mt-1 text-[11px] text-sky-400">
                      Connect another system →
                    </button>
                  </div>
                )}
                {!WIDE.includes(selWidget.type) && selPane.width !== "third" && (
                  <div>
                    <label className={LABEL}>Width in the pane</label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {[1, 2].map((n) => (
                        <button key={n} type="button" onClick={() => updateWidget(selPane.id, selWidget.id, { span: n === 2 ? 2 : undefined })}
                          className={`rounded-md border px-2 py-1.5 text-xs ${(selWidget.span === 2 ? 2 : 1) === n ? "border-sky-500 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-400"}`}>
                          {n === 1 ? "Half" : "Full"}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <div className="flex gap-2 border-t border-slate-800 pt-3">
                  <select value={selPane.id} onChange={(e) => {
                    const to = e.target.value;
                    change((l) => {
                      const src = l.panes.find((p) => p.id === selPane.id)!;
                      const w = src.widgets.find((x) => x.id === selWidget.id)!;
                      src.widgets = src.widgets.filter((x) => x.id !== selWidget.id);
                      l.panes.find((p) => p.id === to)?.widgets.push(w);
                    });
                    setSel({ kind: "widget", pane: to, id: selWidget.id });
                  }} className={INPUT}>
                    {draft.panes.map((p) => (
                      <option key={p.id} value={p.id}>
                        Pane: {p.title} ({tabLabel(paneTab(p))})
                      </option>
                    ))}
                  </select>
                  <button type="button" title="Remove (Delete)"
                    onClick={() => (change((l) => (l.panes = l.panes.map((p) => (p.id === selPane.id ? { ...p, widgets: p.widgets.filter((w) => w.id !== selWidget.id) } : p)))), setSel({ kind: "pane", id: selPane.id }))}
                    className="rounded-md border border-slate-700 px-2.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                    Remove
                  </button>
                </div>
              </div>
            ) : sel.kind === "pane" && selPane ? (
              <div className="space-y-4 p-4">
                <p className="text-sm font-semibold text-slate-100">Pane</p>
                <AudienceEditor value={selPane.audience} onChange={audience => updatePane(selPane.id, { audience })} />
                <div className="flex gap-2">
                  <div className="w-16">
                    <label className={LABEL}>Icon</label>
                    <input value={selPane.icon} onChange={(e) => updatePane(selPane.id, { icon: e.target.value.slice(0, 4) })} className={`${INPUT} text-center`} />
                  </div>
                  <div className="flex-1">
                    <label className={LABEL}>Title</label>
                    <input value={selPane.title} onChange={(e) => updatePane(selPane.id, { title: e.target.value.slice(0, 60) })} className={INPUT} />
                  </div>
                </div>
                <div>
                  <label className={LABEL}>Shown on tab</label>
                  <select value={paneTab(selPane)} onChange={(e) => {
                    const tab = e.target.value as PageTab;
                    updatePane(selPane.id, { tab: tabOfDomain(selPane.domain) === tab ? null : tab });
                    setPageTab(tab);
                  }} className={INPUT}>
                    {PAGE_TABS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className={LABEL}>Data area</label>
                  <select value={selPane.domain} onChange={(e) => {
                    const d = domainMeta(e.target.value);
                    const tab = paneTab(selPane);
                    updatePane(selPane.id, {
                      domain: e.target.value, tab: tabOfDomain(e.target.value) === tab ? null : tab,
                      ...(d && selPane.title === domainMeta(selPane.domain)?.label ? { title: d.label } : {}),
                      ...(d && selPane.icon === domainMeta(selPane.domain)?.icon ? { icon: d.icon } : {}),
                    });
                  }} className={INPUT}>
                    {cat.domains.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
                  </select>
                  <p className="mt-1 text-[11px] text-slate-500">Sets the pane's default icon and colour and which charts are suggested. It stays on the tab chosen above.</p>
                </div>
                <button type="button" onClick={() => updatePane(selPane.id, { collapsed: !selPane.collapsed })} className="flex w-full items-center gap-3 rounded-md px-1 py-1.5 text-left hover:bg-slate-800/50">
                  <span className={`relative h-5 w-9 shrink-0 rounded-full transition ${selPane.collapsed ? "bg-sky-500" : "bg-slate-700"}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${selPane.collapsed ? "left-[18px]" : "left-0.5"}`} />
                  </span>
                  <span className="text-sm text-slate-200">Start collapsed</span>
                </button>
                <div>
                  <label className={LABEL}>Width</label>
                  <div className="grid grid-cols-4 gap-1.5">
                    {(["third", "half", "two_thirds", "full"] as C360Width[]).map((w) => (
                      <button key={w} type="button" onClick={() => updatePane(selPane.id, { width: w })}
                        className={`rounded-md border py-1.5 text-xs font-semibold ${selPane.width === w ? "border-sky-500 bg-sky-500/10 text-sky-200" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
                        {WIDTH_LABELS[w]}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className={LABEL}>Colour</label>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {SWATCHES.map((c) => (
                      <button key={c} type="button" onClick={() => updatePane(selPane.id, { color: c })} title={c}
                        className={`h-6 w-6 rounded-full border-2 ${selPane.color.toLowerCase() === c ? "border-white" : "border-transparent"}`} style={{ background: c }} />
                    ))}
                    <input type="color" value={selPane.color} onChange={(e) => updatePane(selPane.id, { color: e.target.value })} className="h-6 w-8 cursor-pointer rounded border border-slate-700 bg-slate-950" />
                  </div>
                </div>
                <p className="text-xs text-slate-500">{selPane.widgets.length} chart{selPane.widgets.length === 1 ? "" : "s"} - drag more from the left, drag panes to reorder.</p>
                <button type="button" onClick={() => (change((l) => (l.panes = l.panes.filter((p) => p.id !== selPane.id))), setSel({ kind: "page" }))}
                  className="w-full rounded-md border border-slate-700 py-1.5 text-xs text-slate-400 hover:border-red-500 hover:text-red-400">
                  Delete pane
                </button>
              </div>
            ) : (
              <div className="space-y-4 p-4">
                <p className="text-sm font-semibold text-slate-100">Page</p>
                <div className="space-y-3">
                  <label className={LABEL}>Standard component audience</label>
                  <select aria-label="Standard component audience" value={audienceComponent} onChange={e => setAudienceComponent(e.target.value)} className={INPUT}>
                    {Object.entries(STANDARD_COMPONENTS).map(([id, title]) => <option key={id} value={id}>{title}</option>)}
                    {draft.header.kpis.map(id => <option key={id} value={`kpi:${id}`}>Headline: {recipeTitle(id)}</option>)}
                  </select>
                  <AudienceEditor value={draft.component_audiences?.[audienceComponent]} onChange={audience => change(l => { l.component_audiences = { ...l.component_audiences, [audienceComponent]: audience }; })} />
                  <p className="text-[11px] text-slate-500">These rules control the standard page sections. Configure added panes and widgets in their own inspector. Publish once; the selected customer determines what appears.</p>
                </div>
                <div>
                  <label className={LABEL}>Headline numbers (up to 6)</label>
                  <div className="flex flex-wrap gap-1">
                    {cat.header_kpis.map((id) => (
                      <button key={id} type="button"
                        onClick={() => change((l) => (l.header.kpis = kpiSet.has(id) ? l.header.kpis.filter((k) => k !== id) : [...l.header.kpis, id].slice(0, 6)))}
                        className={`rounded-full px-2.5 py-0.5 text-[11px] ${kpiSet.has(id) ? "bg-sky-600 text-white" : "bg-slate-800 text-slate-400 hover:text-slate-200"}`}>
                        {kpiSet.has(id) ? "✓ " : "+ "}
                        {recipeTitle(id)}
                      </button>
                    ))}
                  </div>
                </div>
                <button type="button" onClick={() => change((l) => (l.header.show_health = !l.header.show_health))} className="flex w-full items-center gap-3 rounded-md px-1 py-1.5 text-left hover:bg-slate-800/50">
                  <span className={`relative h-5 w-9 shrink-0 rounded-full transition ${draft.header.show_health ? "bg-sky-500" : "bg-slate-700"}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${draft.header.show_health ? "left-[18px]" : "left-0.5"}`} />
                  </span>
                  <span className="text-sm text-slate-200">Customer health score</span>
                </button>
                <div>
                  <label className={LABEL}>Panes per tab</label>
                  <div className="space-y-1">
                    {PAGE_TABS.map((t) => (
                      <button key={t.id} type="button" onClick={() => setPageTab(t.id)}
                        className={`flex w-full justify-between rounded-md px-2 py-1 text-xs ${pageTab === t.id ? "bg-slate-800 text-white" : "text-slate-400 hover:bg-slate-800/50"}`}>
                        <span>{t.label}</span>
                        <span>{counts[t.id]}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <p className="rounded-lg bg-slate-800/40 p-3 text-xs leading-relaxed text-slate-400">
                  Pick a tab at the top, drag charts from the left into a pane, click anything to change it, or ask the AI. <b>Preview</b> shows the page with real customer data.{" "}
                  <b>Ctrl+Z</b> undoes, <b>Ctrl+S</b> publishes.
                </p>
              </div>
            )}
          </aside>
        </div>
      )}

      {drawer === "look" && studio && draft && (
        <Drawer title="Look & feel" sub="Theme, colours, card style and density" onClose={() => setDrawer(null)}>
          <LookTab studio={studio} draft={draft} setDraft={(next) => change((l) => Object.assign(l, next))} />
        </Drawer>
      )}
      {drawer === "sources" && studio && (
        <Drawer title="Data sources" sub="The billing, service, network and inventory systems the page reads" onClose={() => setDrawer(null)}>
          <SourcesTab studio={studio} pending={pending} setPending={setPending} partyId={partyId} onChanged={() => reload(false)}
            onAgent={(text) => (setDrawer(null), ask(text))} />
        </Drawer>
      )}
      {drawer === "history" && studio && (
        <Drawer title="Published versions" sub="Every publish is kept - restore any of them" onClose={() => setDrawer(null)}>
          <button type="button" className={`${GHOST} mb-3 w-full border border-slate-700`}
            onClick={() => c360Undo().then((r) => (reload(true), setPending([]), notifyIntentExecuted(), notify("ok", `Back on version ${r.version}`))).catch((e) => notify("err", String(e.message).replace(/^\d+ [^:]*: /, "")))}>
            ↶ Undo the last publish
          </button>
          <div className="space-y-1.5">
            {studio.history.map((h) => (
              <div key={h.version} className="rounded-md border border-slate-800 bg-slate-950/50 px-3 py-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-slate-400">v{h.version}</span>
                  {h.version === studio.version && <span className="rounded-full bg-emerald-500/15 px-1.5 text-[10px] text-emerald-300">live</span>}
                  <span className="ml-auto text-[10px] text-slate-500">{new Date(h.created_at).toLocaleString()}</span>
                </div>
                <p className="mt-1 text-slate-300">{h.summary}</p>
                <div className="mt-1 flex items-center">
                  <span className="text-[10px] text-slate-500">{h.changed_by}</span>
                  {h.version !== studio.version && (
                    <button type="button" className="ml-auto text-[11px] text-sky-400"
                      onClick={() => c360Restore(h.version).then(() => (reload(true), notifyIntentExecuted(), setDrawer(null), notify("ok", `Restored version ${h.version}`)))}>
                      Restore
                    </button>
                  )}
                </div>
              </div>
            ))}
            {studio.history.length === 0 && <p className="text-xs text-slate-500">Nothing published yet.</p>}
          </div>
        </Drawer>
      )}
      {drawer === "chat" && (
        <Drawer title="AI conversation" sub={studio?.ai_engine === "ai" ? "Your tenant's AI model" : "Built-in language rules - add an AI key in Admin for free-form requests"} onClose={() => setDrawer(null)}>
          <div className="space-y-2">
            {messages.map((m, i) =>
              m.role === "you" ? (
                <div key={i} className="ml-8 rounded-2xl rounded-br-sm bg-sky-600 px-3 py-2 text-xs text-white">
                  {m.text}
                </div>
              ) : (
                <div key={i} className="mr-4 space-y-1 rounded-2xl rounded-bl-sm border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200">
                  {m.plan?.understood ? m.plan.changes.map((c) => <p key={c}>• {c}</p>) : <p>{m.text}</p>}
                  {m.plan?.notes.map((n) => (
                    <p key={n} className="text-amber-300">
                      ⚠ {n}
                    </p>
                  ))}
                </div>
              )
            )}
          </div>
        </Drawer>
      )}

      {toast && (
        <div className={`fixed bottom-6 left-1/2 z-[80] max-w-xl -translate-x-1/2 rounded-lg px-4 py-2 text-sm shadow-2xl ${toast.tone === "ok" ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}`}>
          {toast.text}
        </div>
      )}
    </div>
  );
}
