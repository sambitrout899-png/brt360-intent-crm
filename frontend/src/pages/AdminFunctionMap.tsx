import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  functionMapActivate,
  functionMapDeleteVersion,
  functionMapMine,
  functionMapNewVersion,
  functionMapPlanActivation,
  functionMapRevise,
  functionMapSaveVersion,
  functionMapSimulate,
  functionMapVanilla,
  functionMapVersion,
} from "../api/client";
import type { FunctionMap, MapChange, MapDraft, MapEdge, MapNode, MapPlan, MapSimulation, MapVersion, MapVersions } from "../api/functionMapTypes";
import ConditionEditor from "./functionMap/ConditionEditor";
import MiniMap from "./functionMap/MiniMap";
import Navigator from "./functionMap/Navigator";
import FunctionPointPicker from "./functionMap/FunctionPointPicker";
import { matchesFunctionPoint } from "./functionMap/search";
import { GUTTER, H, PROCESS_COLORS, W, autoArrange, conditionText, edgeKey, laneBands, midpoint, path, snap, type Box } from "./functionMap/geometry";

/** what is on screen: the map in use, BRT's original, or one of the tenant's versions (its id) */
type Showing = "active" | "original" | number;

const STATUS: Record<MapVersion["status"], { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "border-amber-500/40 bg-amber-500/10 text-amber-200" },
  active: { label: "Active", cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200" },
  retired: { label: "Retired", cls: "border-slate-600 bg-slate-800/60 text-slate-400" },
};
type Selection = { node: string } | { edge: string } | null;
type View = { x: number; y: number; k: number };
type Snapshot = { nodes: MapNode[]; edges: MapEdge[] };
type Aim = { codes: string[]; mode: "center" | "fit"; maxK?: number };

const MIN_K = 0.2;
const MAX_K = 1.6;
const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));
const PREFS = "brt360.functionMap.prefs";

function readPrefs(): { links: "all" | "focus"; collapsed: string[]; nav: boolean } {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    return { links: p.links === "focus" ? "focus" : "all", collapsed: Array.isArray(p.collapsed) ? p.collapsed : [], nav: p.nav !== false };
  } catch {
    return { links: "all", collapsed: [], nav: true };
  }
}

const isTyping = (t: EventTarget | null) => t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

function ago(iso?: string | null): string {
  if (!iso) return "";
  const minutes = Math.round((Date.now() - new Date(iso.endsWith("Z") ? iso : `${iso}Z`).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

function toDraft(nodes: MapNode[], edges: MapEdge[]): MapDraft {
  return {
    nodes: nodes.map(({ code, label, lane, active, x, dy }) => ({ code, label, lane, active, x, dy })),
    edges: edges
      .filter((e) => e.kind === "next" || e.active)
      .map(({ source, target, kind, why, when }) => ({ source, target, kind, why: why ?? "", ...(when ? { when } : {}) })),
  };
}

/** Admin -> Functional Map: every function point of the platform in lanes by
 * business area, joined by the hand-offs of the business processes. BRT's
 * original map and the map in use are read-only. The tenant customizes by
 * making versions (from the original, the map in use or another version),
 * working on drafts, and activating one - activating shows what changes in the
 * engines (intents on/off, names, areas, next steps, automations) and applies
 * it on one consent; the version it replaces is retired, kept to go back to.
 *
 * Getting around: drag the background to pan, Ctrl/⌘ + scroll to zoom, the
 * minimap to jump, the navigator to find a process or area; hovering or
 * selecting a card lights up only its links. */
export default function AdminFunctionMap({ onOpenFlowStudio, onOpenIntents }: { onOpenFlowStudio?: () => void; onOpenIntents?: () => void }) {
  const prefs = useMemo(readPrefs, []);
  const [showing, setShowing] = useState<Showing>("active");
  const [versions, setVersions] = useState<MapVersions | null>(null);
  const [planFor, setPlanFor] = useState<number | "original" | null>(null);
  const [simulation, setSimulation] = useState<MapSimulation | null>(null);
  const [vanilla, setVanilla] = useState<FunctionMap | null>(null);
  const [mine, setMine] = useState<FunctionMap | null>(null);
  const [nodes, setNodes] = useState<MapNode[]>([]);
  const [edges, setEdges] = useState<MapEdge[]>([]);
  const [baseline, setBaseline] = useState("");
  const [selection, setSelection] = useState<Selection>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 0.7 });
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [query, setQuery] = useState("");
  const [hit, setHit] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const [process, setProcess] = useState("");
  const [showNext, setShowNext] = useState(true);
  const [showAuto, setShowAuto] = useState(true);
  const [links, setLinks] = useState<"all" | "focus">(prefs.links);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set(prefs.collapsed));
  const [navOpen, setNavOpen] = useState(prefs.nav);
  const [full, setFull] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [grabbing, setGrabbing] = useState(false);
  const [aim, setAim] = useState<Aim | null>(null);
  const [compare, setCompare] = useState(false);
  const [plan, setPlan] = useState<MapPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [applied, setApplied] = useState<{ changes: MapChange[]; problems: MapPlan["problems"] } | null>(null);
  const [link, setLink] = useState<{ from: string; x: number; y: number } | null>(null);
  const [, setHistoryTick] = useState(0);
  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const lastTag = useRef({ tag: "", at: 0 });
  const viewport = useRef<HTMLDivElement>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const overMap = useRef(false);
  const fitPending = useRef(true);
  const drag = useRef<{ code: string; offX: number; offY: number; bands: { key: string; top: number; height: number }[]; moved: boolean } | null>(null);
  const pan = useRef<{ sx: number; sy: number; x: number; y: number; z: number; moved: boolean } | null>(null);

  const isOriginal = showing === "original";
  const version = typeof showing === "number" ? mine?.version : undefined;
  // only a draft version is ever changed - the map in use and BRT's original are read-only
  const editable = version?.status === "draft";
  const map = isOriginal ? vanilla : mine;
  const shownNodes = useMemo(() => (isOriginal ? vanilla?.nodes ?? [] : nodes), [isOriginal, nodes, vanilla]);
  const shownEdges = useMemo(() => (isOriginal ? vanilla?.edges ?? [] : edges), [isOriginal, edges, vanilla]);
  const dirty = editable && mine !== null && JSON.stringify(toDraft(nodes, edges)) !== baseline;

  useEffect(() => {
    try {
      localStorage.setItem(PREFS, JSON.stringify({ links, collapsed: [...collapsed], nav: navOpen }));
    } catch {
      /* private window - the view resets next visit */
    }
  }, [links, collapsed, navOpen]);

  const load = useCallback((fresh?: FunctionMap) => {
    const take = (m: FunctionMap) => {
      setMine(m);
      setNodes(m.nodes);
      setEdges(m.edges.filter((e) => e.kind === "next" || e.active));
      setBaseline(JSON.stringify(toDraft(m.nodes, m.edges)));
      past.current = [];
      future.current = [];
      setHistoryTick((t) => t + 1);
    };
    if (fresh) return take(fresh);
    if (showing === "original") return;
    const fail = (e: unknown) => setError(e instanceof Error ? e.message : "Couldn't load the map");
    if (showing === "active") {
      functionMapMine()
        .then((m) => {
          setVersions({ versions: m.versions, active_id: m.active_id, on_original: m.on_original });
          take(m);
        })
        .catch(fail);
    } else {
      functionMapVersion(showing).then(take).catch(fail);
    }
  }, [showing]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    functionMapVanilla().then(setVanilla).catch(() => setVanilla(null));
  }, []);

  /** Switch what is on screen - unsaved draft changes are asked about first. */
  function show(next: Showing) {
    if (next === showing) return;
    if (dirty && !window.confirm("This draft has unsaved changes. Leave without saving them?")) return;
    setSelection(null);
    setApplied(null);
    setError("");
    setShowing(next);
  }

  const refreshVersions = () =>
    functionMapMine()
      .then((m) => setVersions({ versions: m.versions, active_id: m.active_id, on_original: m.on_original }))
      .catch(() => undefined);

  // ---------- undo / redo ----------
  /** Keep the map as it is now so the next change can be undone. Repeated
   * changes with the same tag (typing a name) count as one step. */
  function remember(tag = "") {
    const now = Date.now();
    if (tag && lastTag.current.tag === tag && now - lastTag.current.at < 1200) {
      lastTag.current.at = now;
      return;
    }
    past.current.push({ nodes, edges });
    if (past.current.length > 100) past.current.shift();
    future.current = [];
    lastTag.current = { tag, at: now };
    setHistoryTick((t) => t + 1);
  }

  function undo() {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push({ nodes, edges });
    setNodes(prev.nodes);
    setEdges(prev.edges);
    lastTag.current = { tag: "", at: 0 };
    setHistoryTick((t) => t + 1);
  }

  function redo() {
    const next = future.current.pop();
    if (!next) return;
    past.current.push({ nodes, edges });
    setNodes(next.nodes);
    setEdges(next.edges);
    lastTag.current = { tag: "", at: 0 };
    setHistoryTick((t) => t + 1);
  }

  // ---------- geometry ----------
  const bands = useMemo(() => (map ? laneBands(map.lanes, shownNodes, collapsed) : []), [map, shownNodes, collapsed]);
  const bandOf = useMemo(() => Object.fromEntries(bands.map((b) => [b.key, b])), [bands]);
  const laneTop = useMemo(() => Object.fromEntries(bands.map((b) => [b.key, b.top])), [bands]);
  const place = useCallback((n: MapNode) => ({ left: GUTTER + n.x, top: (bandOf[n.lane]?.top ?? 0) + n.dy }), [bandOf]);
  const byCode = useMemo(() => Object.fromEntries(shownNodes.map((n) => [n.code, n])), [shownNodes]);
  const visibleNodes = useMemo(() => shownNodes.filter((n) => !collapsed.has(n.lane)), [shownNodes, collapsed]);
  const drawn = useMemo(() => new Set(visibleNodes.map((n) => n.code)), [visibleNodes]);
  const width = Math.max(1200, GUTTER + Math.max(0, ...shownNodes.map((n) => n.x)) + W + 120);
  const height = Math.max(1, bands.reduce((s, b) => s + b.height, 0));
  const labelW = Math.max(84, Math.min(136, GUTTER * view.k - 10));

  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const boxOf = useCallback(
    (codes: string[]): Box | null => {
      const ps = codes.filter((c) => byCode[c]).map((c) => place(byCode[c]));
      if (!ps.length) return null;
      return {
        x0: Math.min(...ps.map((p) => p.left)) - 40,
        y0: Math.min(...ps.map((p) => p.top)) - 30,
        x1: Math.max(...ps.map((p) => p.left)) + W + 40,
        y1: Math.max(...ps.map((p) => p.top)) + H + 30,
      };
    },
    [byCode, place],
  );

  const fitTo = useCallback(
    (b: Box, maxK = 1, minK = MIN_K) => {
      const { w, h } = size;
      if (!w || !h) return;
      const pad = 24;
      const left = 96; // keep clear of the lane names
      const bw = b.x1 - b.x0;
      const bh = b.y1 - b.y0;
      const k = Math.min(MAX_K, Math.max(minK, Math.min(maxK, (w - left - pad) / bw, (h - 2 * pad) / bh)));
      const x = bw * k <= w - left - pad ? left + (w - left - pad - bw * k) / 2 - b.x0 * k : pad - b.x0 * k;
      const y = bh * k <= h - 2 * pad ? (h - bh * k) / 2 - b.y0 * k : pad - b.y0 * k;
      setView({ x, y, k });
    },
    [size],
  );

  const fitAll = useCallback(() => fitTo({ x0: 0, y0: 0, x1: width, y1: height }, 1), [fitTo, width, height]);

  function zoomBy(f: number) {
    setView((v) => {
      const k = clampK(v.k * f);
      const cx = size.w / 2;
      const cy = size.h / 2;
      return { k, x: cx - ((cx - v.x) * k) / v.k, y: cy - ((cy - v.y) * k) / v.k };
    });
  }

  // first look: the whole map, still readable
  useEffect(() => {
    if (!fitPending.current || !map || !size.w) return;
    fitPending.current = false;
    fitTo({ x0: 0, y0: 0, x1: width, y1: height }, 0.9, 0.45);
  }, [map, size.w, width, height, fitTo]);

  // jump to (or frame) some cards - unfolding their areas first
  useEffect(() => {
    if (!aim || !size.w) return;
    const lanes = new Set(aim.codes.map((c) => byCode[c]?.lane).filter((l): l is string => !!l && collapsed.has(l)));
    if (lanes.size) {
      setCollapsed((s) => new Set([...s].filter((l) => !lanes.has(l))));
      return; // runs again once the lanes are open
    }
    if (aim.mode === "center") {
      const n = byCode[aim.codes[0]];
      if (n) {
        const p = place(n);
        setView((v) => {
          const k = Math.max(v.k, 0.6);
          return { k, x: size.w / 2 - (p.left + W / 2) * k, y: size.h / 2 - (p.top + H / 2) * k };
        });
      }
    } else {
      const b = boxOf(aim.codes);
      if (b) fitTo(b, aim.maxK ?? 1);
    }
    setAim(null);
  }, [aim, bands, size, byCode, collapsed, place, boxOf, fitTo]);

  // wheel: scroll pans, Ctrl/⌘ + scroll (or a pinch) zooms at the pointer
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : 1;
      const r = el.getBoundingClientRect();
      const z = r.width / el.offsetWidth || 1;
      const px = (e.clientX - r.left) / z;
      const py = (e.clientY - r.top) / z;
      if (e.ctrlKey || e.metaKey) {
        setView((v) => {
          const k = clampK(v.k * Math.exp(-e.deltaY * unit * 0.0015));
          return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
        });
      } else {
        const dx = (e.shiftKey ? e.deltaY : e.deltaX) * unit;
        const dy = (e.shiftKey ? 0 : e.deltaY) * unit;
        setView((v) => ({ ...v, x: v.x - dx, y: v.y - dy }));
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // ---------- processes, search, compare ----------
  const processColor = useMemo(() => Object.fromEntries((map?.processes ?? []).map((p, i) => [p.key, PROCESS_COLORS[i % PROCESS_COLORS.length]])), [map]);
  const processesOf = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const p of map?.processes ?? []) for (const s of p.steps) (out[s] ??= []).push(p.key);
    return out;
  }, [map]);
  const activeProcess = (map?.processes ?? []).find((p) => p.key === process);
  const steps = useMemo(() => activeProcess?.steps ?? [], [activeProcess]);
  const inProcess = useMemo(() => new Set(steps), [steps]);

  function pickProcess(key: string) {
    setProcess(key);
    setSelection(null);
    const p = map?.processes.find((x) => x.key === key);
    if (p) setAim({ codes: p.steps.filter((c) => byCode[c]), mode: "fit", maxK: 1 });
  }

  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hits = useMemo(
    () => (terms.length ? shownNodes.filter((n) => matchesFunctionPoint(n, query)) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [query, shownNodes],
  );
  const hitSet = new Set(hits.map((n) => n.code));

  const vanillaNodes = useMemo(() => Object.fromEntries((vanilla?.nodes ?? []).map((n) => [n.code, n])), [vanilla]);
  const vanillaLinks = useMemo(() => new Set((vanilla?.edges ?? []).map((e) => `${e.source}>${e.target}`)), [vanilla]);
  const changedFromVanilla = (n: MapNode) => {
    const v = vanillaNodes[n.code];
    return !v || v.label !== n.label || v.lane !== n.lane || !n.active;
  };
  const removedLinks = useMemo(() => {
    if (isOriginal || !compare || !vanilla) return [];
    const mineLinks = new Set(edges.filter((e) => e.kind === "next").map((e) => `${e.source}>${e.target}`));
    return vanilla.edges.filter((e) => !mineLinks.has(`${e.source}>${e.target}`) && drawn.has(e.source) && drawn.has(e.target));
  }, [isOriginal, compare, vanilla, edges, drawn]);

  function jumpTo(code: string) {
    setSelection({ node: code });
    setAim({ codes: [code], mode: "center" });
  }

  useEffect(() => {
    if (hits.length) setAim({ codes: [hits[Math.min(hit, hits.length - 1)].code], mode: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, hit]);

  function toggleLane(key: string) {
    setCollapsed((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  // ---------- editing ----------
  const setNode = (code: string, patch: Partial<MapNode>) => setNodes((all) => all.map((n) => (n.code === code ? { ...n, ...patch } : n)));

  function addEdge(source: string, target: string, kind: MapEdge["kind"] = "next") {
    if (source === target) return;
    if (edges.some((e) => e.source === source && e.target === target && e.kind === kind)) return setSelection({ edge: edgeKey({ source, target, kind }) });
    remember();
    setEdges((all) => [...all, { source, target, kind, why: "", active: true, by_map: true }]);
    setSelection({ edge: edgeKey({ source, target, kind }) });
  }

  function removeEdge(edge: MapEdge) {
    remember();
    setEdges((all) => all.filter((e) => edgeKey(e) !== edgeKey(edge)));
    setSelection(null);
  }

  function tidy() {
    remember();
    setNodes(autoArrange(nodes, edges, (map?.lanes ?? []).map((l) => l.key)));
    fitPending.current = true;
  }

  function toCanvas(e: { clientX: number; clientY: number }) {
    const el = viewport.current!;
    const r = el.getBoundingClientRect();
    const z = r.width / el.offsetWidth || 1;
    return { x: ((e.clientX - r.left) / z - view.x) / view.k, y: ((e.clientY - r.top) / z - view.y) / view.k };
  }

  function nodeAt(x: number, y: number): string | null {
    for (const n of visibleNodes) {
      const p = place(n);
      if (x >= p.left && x <= p.left + W && y >= p.top && y <= p.top + H) return n.code;
    }
    return null;
  }

  useEffect(() => {
    function move(e: PointerEvent) {
      const d = drag.current;
      const pn = pan.current;
      if (d) {
        const p = toCanvas(e);
        const left = p.x - d.offX;
        const top = p.y - d.offY;
        const middle = top + H / 2;
        const band = d.bands.find((b, i) => middle < b.top + b.height || i === d.bands.length - 1) ?? d.bands[0];
        if (!band) return;
        if (!d.moved) {
          d.moved = true;
          remember();
        }
        setNode(d.code, { x: Math.max(0, Math.round(left - GUTTER)), lane: band.key, dy: Math.max(6, Math.round(top - band.top)) });
      } else if (link) {
        const p = toCanvas(e);
        setLink({ ...link, x: p.x, y: p.y });
      } else if (pn) {
        const dx = (e.clientX - pn.sx) / pn.z;
        const dy = (e.clientY - pn.sy) / pn.z;
        if (Math.abs(dx) + Math.abs(dy) > 3) pn.moved = true;
        if (pn.moved) setView((v) => ({ ...v, x: pn.x + dx, y: pn.y + dy }));
      }
    }
    function up(e: PointerEvent) {
      const d = drag.current;
      const pn = pan.current;
      if (d) {
        drag.current = null;
        if (d.moved) setNodes((all) => all.map((n) => (n.code === d.code ? { ...n, x: snap(n.x), dy: Math.max(6, snap(n.dy)) } : n)));
      } else if (link) {
        const p = toCanvas(e);
        const target = nodeAt(p.x, p.y);
        if (target && target !== link.from) addEdge(link.from, target);
        setLink(null);
      } else if (pn) {
        pan.current = null;
        setGrabbing(false);
        if (!pn.moved) setSelection(null);
      }
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  });

  function startPan(e: React.PointerEvent) {
    if ((e.button !== 0 && e.button !== 1) || (e.target as Element).closest("[data-fm-card],[data-fm-edge],[data-fm-ui]")) return;
    const el = viewport.current!;
    pan.current = { sx: e.clientX, sy: e.clientY, x: view.x, y: view.y, z: el.getBoundingClientRect().width / el.offsetWidth || 1, moved: false };
    setGrabbing(true);
    e.preventDefault();
  }

  function startDrag(e: React.PointerEvent, n: MapNode) {
    setSelection({ node: n.code });
    if (!editable || e.button !== 0) return;
    const p = toCanvas(e);
    const at = place(n);
    drag.current = {
      code: n.code,
      offX: p.x - at.left,
      offY: p.y - at.top,
      bands: bands.filter((b) => !b.collapsed).map(({ key, top, height }) => ({ key, top, height })),
      moved: false,
    };
    e.preventDefault();
  }

  function startLink(e: React.PointerEvent, n: MapNode) {
    e.stopPropagation();
    e.preventDefault();
    const at = place(n);
    setLink({ from: n.code, x: at.left + W, y: at.top + H / 2 });
  }

  /** Double-click a card: frame it with everything it hands to or from. */
  function frameAround(code: string) {
    const near = shownEdges.filter((e) => e.source === code || e.target === code).flatMap((e) => [e.source, e.target]);
    setSelection({ node: code });
    setAim({ codes: [code, ...near], mode: "fit", maxK: 1.1 });
  }

  // ---------- versions: save a draft, make one, activate one ----------
  async function work<T>(job: () => Promise<T>, failed: string): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await job();
    } catch (e) {
      setError(e instanceof Error ? e.message : failed);
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (typeof showing !== "number") return;
    const out = await work(() => functionMapSaveVersion(showing, toDraft(nodes, edges)), "Saving the draft failed");
    if (out) {
      load(out);
      refreshVersions();
    }
  }

  /** A new draft copied from the original, the map in use or a version - then it is on screen to work on. */
  async function newVersion(source: "original" | "active" | number) {
    if (dirty && !window.confirm("This draft has unsaved changes. Leave without saving them?")) return;
    const name = window.prompt("Name the new version", "")?.trim();
    if (name === undefined) return;
    const made = await work(() => functionMapNewVersion(source, name), "Couldn't make the version");
    if (made?.version) {
      await refreshVersions();
      setSelection(null);
      setApplied(null);
      setShowing(made.version.id);
    }
  }

  /** The next revision of the version on screen (v3 -> v3.1) - then it is on screen to work on. */
  async function revise() {
    if (!version) return;
    if (dirty && !window.confirm("This draft has unsaved changes. Leave without saving them?")) return;
    const made = await work(() => functionMapRevise(version.id), "Couldn't make the revision");
    if (made?.version) {
      await refreshVersions();
      setSelection(null);
      setApplied(null);
      setShowing(made.version.id);
    }
  }

  async function rename() {
    if (!version) return;
    const name = window.prompt("Rename this version", version.name)?.trim();
    if (!name || name === version.name) return;
    const out = await work(() => functionMapSaveVersion(version.id, { name }), "Renaming failed");
    if (out) {
      setMine(out);
      refreshVersions();
    }
  }

  async function removeVersion() {
    if (!version || !window.confirm(`Delete “${version.name}”? This can't be undone.`)) return;
    const ok = await work(() => functionMapDeleteVersion(version.id).then(() => true), "Deleting failed");
    if (ok) {
      await refreshVersions();
      setShowing("active");
    }
  }

  /** The version's processes built and played through, then rolled back (a draft is saved first). */
  async function simulate() {
    if (typeof showing !== "number") return;
    if (dirty) {
      const saved = await work(() => functionMapSaveVersion(showing, toDraft(nodes, edges)), "Saving the draft failed");
      if (!saved) return;
      load(saved);
    }
    const out = await work(() => functionMapSimulate(showing), "The simulation didn't run");
    if (out) setSimulation(out);
  }

  /** Shows what activating would change (a draft is saved first). */
  async function review(target: number | "original") {
    if (dirty && typeof showing === "number") {
      const saved = await work(() => functionMapSaveVersion(showing, toDraft(nodes, edges)), "Saving the draft failed");
      if (!saved) return;
      load(saved);
    }
    const planned = await work(() => functionMapPlanActivation(target), "Couldn't work out the changes");
    if (planned) {
      setPlanFor(target);
      setPlan(planned);
    }
  }

  async function confirm() {
    if (planFor === null) return;
    const out = await work(() => functionMapActivate(planFor), "Activating failed - nothing was changed");
    if (out) {
      setApplied({ changes: out.applied, problems: out.problems });
      setVersions({ versions: out.versions, active_id: out.active_id, on_original: out.on_original });
      setPlan(null);
      setPlanFor(null);
      setSelection(null);
      if (showing === "active") load(out.map);
      else setShowing("active");
    }
  }

  // ---------- render ----------
  const selectedNode = selection && "node" in selection ? byCode[selection.node] : undefined;
  const selectedEdge = selection && "edge" in selection ? shownEdges.find((e) => edgeKey(e) === selection.edge) : undefined;
  const focusCode = selectedNode?.code ?? (grabbing || link ? null : hover);
  const related = useMemo(
    () => (focusCode ? new Set(shownEdges.filter((e) => e.source === focusCode || e.target === focusCode).flatMap((e) => [e.source, e.target])) : null),
    [focusCode, shownEdges],
  );
  const sortedNodes = useMemo(() => [...shownNodes].sort((a, b) => a.label.localeCompare(b.label)), [shownNodes]);
  const visibleEdges = shownEdges.filter((e) => drawn.has(e.source) && drawn.has(e.target) && (e.kind === "next" ? showNext : showAuto && e.active !== false));
  const canEditEdge = (e?: MapEdge) => !!e && editable && !(e.kind === "auto" && e.by_map === false);

  // keyboard: Esc, Delete, F (fit), + / − / 0 (zoom), arrows (pan), / (find), Ctrl+Z / Ctrl+Y
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e.target) || plan) return;
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && !e.altKey && (key === "z" || key === "y")) {
        if (!editable) return;
        e.preventDefault();
        if (key === "y" || e.shiftKey) redo();
        else undo();
        return;
      }
      if (mod || e.altKey) return;
      if (e.key === "Escape") {
        if (link) setLink(null);
        else if (selection) setSelection(null);
        else if (process) setProcess("");
        else if (full) setFull(false);
        return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && canEditEdge(selectedEdge)) {
        e.preventDefault();
        removeEdge(selectedEdge!);
        return;
      }
      if (!overMap.current && !full) return;
      const step = 90;
      const moves: Record<string, () => void> = {
        f: fitAll,
        "+": () => zoomBy(1.2),
        "=": () => zoomBy(1.2),
        "-": () => zoomBy(1 / 1.2),
        "0": () => zoomBy(1 / view.k),
        "/": () => searchBox.current?.focus(),
        arrowleft: () => setView((v) => ({ ...v, x: v.x + step })),
        arrowright: () => setView((v) => ({ ...v, x: v.x - step })),
        arrowup: () => setView((v) => ({ ...v, y: v.y + step })),
        arrowdown: () => setView((v) => ({ ...v, y: v.y - step })),
      };
      const act = moves[key];
      if (act) {
        e.preventDefault();
        act();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const nodeTint = (n: MapNode) =>
    selectedNode?.code === n.code ? "#38bdf8" : hitSet.has(n.code) ? "#fbbf24" : process ? (inProcess.has(n.code) ? processColor[process] : "#1e293b") : n.active ? "#475569" : "#1e293b";

  const btn = "rounded-md border border-slate-700 px-2.5 py-1.5 text-xs text-slate-300 hover:border-slate-500 hover:text-slate-100 disabled:opacity-40 disabled:hover:border-slate-700";
  const heightCls = full ? "h-full" : "h-[72vh] min-h-[460px]";

  return (
    <div className="space-y-3">
      {/* ---------- header ---------- */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold text-slate-100">🗺️ Functional Map</h2>
          <p className="text-xs text-slate-400">
            Function points for your selected modules, by business area, joined by the hand-offs of your processes. The map in use and BRT's original are
            read-only: make a version, work on it as a draft, and activate it - you see every change in the engines before it is made.
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          Showing
          <select
            value={String(showing)}
            onChange={(e) => show(e.target.value === "active" || e.target.value === "original" ? e.target.value : Number(e.target.value))}
            className="max-w-[22rem] rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs font-semibold text-slate-100 focus:border-sky-500 focus:outline-none"
          >
            <option value="active">▶ The map in use</option>
            <option value="original">🔒 BRT original</option>
            {(versions?.versions ?? []).map((v) => (
              <option key={v.id} value={v.id}>
                {v.label ?? `v${v.number}`} · {v.name} - {STATUS[v.status].label}
                {v.locked_by ? ` · 🔒 ${v.locked_by.user}` : ""}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* ---------- what is on screen, and what can be done with it ---------- */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
        {showing === "active" && (
          <>
            <span className="min-w-0 flex-1">
              🔒 <span className="font-semibold text-slate-200">The map your business runs</span> - read-only.{" "}
              {versions && !versions.on_original
                ? (() => {
                    const a = versions.versions.find((v) => v.id === versions.active_id);
                    return a ? `Version ${a.number} “${a.name}”, activated ${ago(a.activated_at)}${a.activated_by ? ` by ${a.activated_by}` : ""}.` : "";
                  })()
                : "It is BRT's original map - BRT's releases reach it as they come."}{" "}
              {(mine?.differences ?? []).length} difference(s) from the original.
            </span>
            <button onClick={() => newVersion("active")} disabled={busy} className={btn}>
              ＋ New version from this map
            </button>
          </>
        )}
        {isOriginal && (
          <>
            <span className="min-w-0 flex-1">
              🔒 <span className="font-semibold text-slate-200">BRT's original map</span> - the standard function points and processes for your selected modules.
              It is never changed by an organization; BRT's releases update it for everyone.
            </span>
            <button onClick={() => newVersion("original")} disabled={busy} className={btn}>
              ＋ New version from the original
            </button>
            {versions && !versions.on_original && (
              <button onClick={() => review("original")} disabled={busy} className={`${btn} hover:border-amber-500/60`}>
                ↺ Go back to the original…
              </button>
            )}
          </>
        )}
        {version && (
          <>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${STATUS[version.status].cls}`}>{STATUS[version.status].label}</span>
            <span className="min-w-0 flex-1">
              <span className="font-semibold text-slate-200">
                {version.label ?? `v${version.number}`} · {version.name}
              </span>{" "}
              - from {version.based_on}
              {version.locked_by && ` · 🔒 being worked on by ${version.locked_by.user} - others revise or copy it`}
              {version.status === "draft" && `, saved ${ago(version.updated_at)}${dirty ? " · unsaved changes" : ""}. Nothing runs from a draft until it is activated.`}
              {version.status === "active" && ` - active since ${ago(version.activated_at)}. Read-only: to change it, make a new version from it.`}
              {version.status === "retired" && " - no longer in use; kept to activate again."}
            </span>
            {version.status === "draft" && (
              <button onClick={rename} disabled={busy} className={btn}>
                Rename
              </button>
            )}
            <button onClick={revise} disabled={busy} className={btn} title="The next revision of this version (keeps its number) - a draft to work on">
              ✎ Revise
            </button>
            <button onClick={() => newVersion(version.id)} disabled={busy} className={btn}>
              ＋ Copy as a new version
            </button>
            {version.status !== "active" && (
              <>
                <button onClick={removeVersion} disabled={busy} className={`${btn} hover:border-red-500/60 hover:text-red-300`}>
                  Delete
                </button>
                {(mine?.builds ?? []).some((b) => b.status === "pending") && (
                  <button onClick={simulate} disabled={busy} className={`${btn} border-violet-500/40 text-violet-200 hover:border-violet-400`}>
                    ▶ Simulate
                  </button>
                )}
                <button
                  onClick={() => review(version.id)}
                  disabled={busy}
                  className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-40"
                >
                  {version.status === "retired" ? "Activate again…" : "Activate…"}
                </button>
              </>
            )}
          </>
        )}
      </div>
      {version && (mine?.builds ?? []).length > 0 && (
        <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2 text-xs text-slate-300">
          <p className="font-semibold text-emerald-200">Processes in this version (from the Process Builder)</p>
          <ul className="mt-1 space-y-1">
            {mine!.builds!.map((b) => (
              <li key={b.id} className="flex flex-wrap items-baseline gap-x-2">
                <span className={`rounded-full px-1.5 text-[10px] font-semibold ${b.status === "built" ? "bg-emerald-500/20 text-emerald-200" : "bg-amber-500/20 text-amber-200"}`}>
                  {b.status === "built" ? "Built" : "Built on activation"}
                </span>
                <span className="font-medium text-slate-100">{b.name}</span>
                <span className="text-slate-500">
                  {b.placement?.mode === "append"
                    ? `added to ${map?.processes.find((p) => p.key === b.placement?.process_key)?.label ?? "a process"}`
                    : `a new process “${b.placement?.label ?? b.name}”`}{" "}
                  · {b.pieces.map((p) => p.title).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(mine?.release_notes ?? []).length > 0 && !isOriginal && (
        <div className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-100">
          <p className="font-semibold">BRT has released changes since this version was saved - they are already in it:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {mine!.release_notes!.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      {applied && (
        <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
          <div className="flex items-start gap-2">
            <p className="flex-1 font-semibold">
              ✓ Activated - the business runs on it now{applied.changes.length ? `; ${applied.changes.length} change(s) made in the engines` : ""}
            </p>
            <button onClick={() => setApplied(null)} className="text-emerald-300/70 hover:text-emerald-100">
              ✕
            </button>
          </div>
          {applied.changes.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-emerald-100/90">
              {applied.changes.map((c, i) => (
                <li key={i}>{c.text}</li>
              ))}
            </ul>
          )}
          {applied.problems.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-amber-200">
              {applied.problems.map((p, i) => (
                <li key={i}>Not done: {p.text}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className={full ? "fixed inset-0 z-50 flex flex-col gap-3 bg-slate-950 p-3" : "space-y-3"}>
        {/* ---------- toolbar ---------- */}
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-2">
          <button onClick={() => setNavOpen((o) => !o)} className={`${btn} ${navOpen ? "border-sky-500/50 text-sky-300" : ""}`} title="Show or hide the list of processes and areas">
            ☰ Navigator
          </button>
          <div className="relative">
            <input
              ref={searchBox}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setHit(0);
                setSearchOpen(true);
              }}
              onFocus={() => setSearchOpen(true)}
              onBlur={() => setSearchOpen(false)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && hits.length) {
                  setHit((h) => (h + 1) % hits.length);
                  setSelection({ node: hits[(hit + 1) % hits.length].code });
                }
                if (e.key === "Escape") {
                  setQuery("");
                  e.currentTarget.blur();
                }
              }}
              placeholder="Find a function point…  ( / )"
              className="w-60 rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
            />
            {terms.length > 0 && (
              <span className="absolute right-2 top-1.5 text-[10px] text-slate-500">{hits.length ? `${Math.min(hit, hits.length - 1) + 1}/${hits.length}` : "none"}</span>
            )}
            {searchOpen && hits.length > 0 && (
              <ul className="absolute left-0 top-full z-40 mt-1 max-h-72 w-80 overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 p-1 shadow-xl shadow-black/40">
                {hits.slice(0, 12).map((n, i) => (
                  <li key={n.code}>
                    <button
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setHit(i);
                        jumpTo(n.code);
                        setSearchOpen(false);
                      }}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs ${i === Math.min(hit, hits.length - 1) ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800"}`}
                    >
                      <span>{n.icon}</span>
                      <span className="min-w-0 flex-1 truncate">{n.label}</span>
                      <span className="shrink-0 text-[10px] text-slate-500">{bandOf[n.lane]?.label ?? n.lane}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="flex rounded-md border border-slate-700 p-0.5 text-[11px] font-semibold" role="group" aria-label="Which links to draw" title="Focus: draw links only for the card you point at or pick, or the process you highlight">
            {(
              [
                ["all", "All links"],
                ["focus", "Focus links"],
              ] as const
            ).map(([k, label]) => (
              <button key={k} onClick={() => setLinks(k)} aria-pressed={links === k} className={`rounded px-2 py-1 ${links === k ? "bg-slate-700 text-white" : "text-slate-400 hover:text-slate-200"}`}>
                {label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1 text-xs text-slate-400">
            <input type="checkbox" checked={showNext} onChange={(e) => setShowNext(e.target.checked)} /> Next steps
          </label>
          <label className="flex items-center gap-1 text-xs text-slate-400">
            <input type="checkbox" checked={showAuto} onChange={(e) => setShowAuto(e.target.checked)} /> ⚡ Automations
          </label>
          {!isOriginal && (
            <label className="flex items-center gap-1 text-xs text-slate-400" title="Mark what differs from BRT's original map">
              <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare with the original
            </label>
          )}
          <div className="ml-auto flex items-center gap-1 text-xs text-slate-400">
            <button onClick={() => zoomBy(1 / 1.2)} className="rounded border border-slate-700 px-2 py-1 hover:text-slate-200" title="Zoom out ( − )">
              −
            </button>
            <button onClick={() => zoomBy(1 / view.k)} className="w-12 rounded py-1 text-center hover:text-slate-200" title="Actual size ( 0 )">
              {Math.round(view.k * 100)}%
            </button>
            <button onClick={() => zoomBy(1.2)} className="rounded border border-slate-700 px-2 py-1 hover:text-slate-200" title="Zoom in ( + )">
              +
            </button>
            <button onClick={fitAll} className="rounded border border-slate-700 px-2 py-1 hover:text-slate-200" title="Show the whole map ( F )">
              Fit
            </button>
            <button onClick={() => setFull((f) => !f)} className="rounded border border-slate-700 px-2 py-1 hover:text-slate-200" title={full ? "Exit full screen (Esc)" : "Full screen"}>
              {full ? "⤡" : "⛶"}
            </button>
          </div>
          {editable && (
            <div className="flex w-full flex-wrap items-center gap-2 border-t border-slate-800 pt-2">
              <button onClick={undo} disabled={!past.current.length} className={btn} title="Undo (Ctrl+Z)">
                ↶ Undo
              </button>
              <button onClick={redo} disabled={!future.current.length} className={btn} title="Redo (Ctrl+Y)">
                ↷ Redo
              </button>
              <button onClick={tidy} className={btn} title="Line the cards up by the order of the hand-offs, area by area. Undo puts them back.">
                ✨ Tidy up
              </button>
              <span className="ml-auto text-[11px] text-slate-500">Editing a draft - nothing changes in the engines until it is activated</span>
              {dirty && (
                <button onClick={() => load()} className={btn}>
                  Discard
                </button>
              )}
              <button
                onClick={saveDraft}
                disabled={!dirty || busy}
                className="rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-500 disabled:opacity-40"
              >
                {busy ? "Working…" : "Save draft"}
              </button>
            </div>
          )}
        </div>

        {activeProcess && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-1.5 text-xs">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: processColor[activeProcess.key] }} />
            <span className="font-semibold text-slate-100">{activeProcess.label}</span>
            <span className="text-slate-400">· {steps.length} steps, numbered on the map</span>
            <button onClick={() => setAim({ codes: steps, mode: "fit", maxK: 1 })} className="ml-auto text-sky-400 hover:text-sky-300">
              Frame it
            </button>
            <button onClick={() => setProcess("")} className="text-slate-400 hover:text-slate-200">
              ✕ Clear
            </button>
          </div>
        )}

        <div className={`flex flex-col gap-3 xl:flex-row ${full ? "min-h-0 flex-1" : ""}`}>
          {navOpen && map && (
            <div className={`shrink-0 xl:w-64 ${full ? "h-64 xl:h-full" : "h-64 xl:h-[72vh] xl:min-h-[460px]"}`}>
              <Navigator
                processes={map.processes}
                processColor={processColor}
                bands={bands}
                nodes={byCode}
                process={process}
                selected={selectedNode?.code}
                onProcess={(k) => (k ? pickProcess(k) : setProcess(""))}
                onNode={jumpTo}
                onToggleLane={toggleLane}
                onAllLanes={(fold) => setCollapsed(fold ? new Set(bands.map((b) => b.key)) : new Set())}
              />
            </div>
          )}

          {/* ---------- the canvas ---------- */}
          <div
            ref={viewport}
            onPointerDown={startPan}
            onPointerEnter={() => (overMap.current = true)}
            onPointerLeave={() => {
              overMap.current = false;
              setHover(null);
            }}
            className={`relative min-w-0 flex-1 overflow-hidden rounded-xl border border-slate-800 bg-slate-950 ${heightCls} ${grabbing ? "cursor-grabbing" : "cursor-grab"}`}
            style={{ touchAction: "none" }}
          >
            {!map ? (
              <p className="p-6 text-sm text-slate-500">Loading the map…</p>
            ) : (
              <>
                <div className="absolute left-0 top-0 origin-top-left select-none" style={{ width, height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>
                  {/* lanes */}
                  {bands.map((b, i) => (
                    <div
                      key={b.key}
                      className={`absolute left-0 border-b border-slate-800/80 ${b.collapsed ? "bg-slate-900/60" : i % 2 ? "bg-slate-900/30" : "bg-slate-900/10"}`}
                      style={{ top: b.top, height: b.height, width }}
                    >
                      {b.collapsed && (
                        <span className="absolute top-1/2 -translate-y-1/2 text-[12px] text-slate-500" style={{ left: GUTTER }}>
                          {b.count} function points folded away
                        </span>
                      )}
                    </div>
                  ))}

                  {/* links */}
                  <svg className="pointer-events-none absolute left-0 top-0 overflow-visible" width={width} height={height}>
                    <defs>
                      {[
                        ["next", "#64748b"],
                        ["hot", "#38bdf8"],
                        ["auto", "#fbbf24"],
                        ["mine", "#a78bfa"],
                        ["gone", "#f87171"],
                        ...Object.entries(processColor).map(([k, c]) => [`p-${k}`, c]),
                      ].map(([id, color]) => (
                        <marker key={id} id={`fm-${id}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                          <path d="M0,0 L10,5 L0,10 z" fill={color} />
                        </marker>
                      ))}
                    </defs>
                    {removedLinks.map((e) => {
                      const a = place(byCode[e.source]);
                      const b = place(byCode[e.target]);
                      return (
                        <path
                          key={`gone-${e.source}-${e.target}`}
                          d={path(a.left + W, a.top + H / 2, b.left, b.top + H / 2)}
                          fill="none"
                          stroke="#f87171"
                          strokeOpacity={0.45}
                          strokeWidth={1.5}
                          strokeDasharray="2 5"
                          markerEnd="url(#fm-gone)"
                        />
                      );
                    })}
                    {visibleEdges.map((e) => {
                      const a = place(byCode[e.source]);
                      const b = place(byCode[e.target]);
                      const key = edgeKey(e);
                      const selected = selection && "edge" in selection && selection.edge === key;
                      const near = !!focusCode && (e.source === focusCode || e.target === focusCode);
                      const inProc = !!process && inProcess.has(e.source) && inProcess.has(e.target);
                      const opacity = selected
                        ? 1
                        : process
                          ? inProc ? 1 : near ? 0.9 : links === "focus" ? 0 : 0.06
                          : focusCode
                            ? near ? 1 : links === "focus" ? 0 : 0.07
                            : links === "focus" ? 0 : e.kind === "auto" ? 0.85 : 0.5;
                      if (!opacity) return null;
                      const yours = !isOriginal && compare && e.kind === "next" && !vanillaLinks.has(`${e.source}>${e.target}`);
                      const color = e.kind === "auto" ? "#fbbf24" : selected || near ? "#38bdf8" : yours ? "#a78bfa" : inProc ? processColor[process] : "#64748b";
                      const marker = e.kind === "auto" ? "auto" : selected || near ? "hot" : yours ? "mine" : inProc ? `p-${process}` : "next";
                      const d = path(a.left + W, a.top + H / 2, b.left, b.top + H / 2);
                      return (
                        <g key={key} opacity={opacity}>
                          <path
                            d={d}
                            fill="none"
                            stroke="transparent"
                            strokeWidth={12}
                            data-fm-edge=""
                            className={opacity > 0.2 ? "pointer-events-auto cursor-pointer" : ""}
                            onPointerDown={(ev) => {
                              ev.stopPropagation();
                              setSelection({ edge: key });
                            }}
                          >
                            <title>
                              {byCode[e.source]?.label} → {byCode[e.target]?.label}
                              {e.why ? ` - ${e.why}` : ""}
                            </title>
                          </path>
                          <path
                            d={d}
                            fill="none"
                            stroke={color}
                            strokeWidth={selected ? 3 : e.kind === "auto" || inProc || near ? 2.2 : 1.6}
                            strokeDasharray={e.kind === "auto" ? "7 4" : undefined}
                            markerEnd={`url(#fm-${marker})`}
                          />
                          {e.kind === "auto" && (
                            <text x={(a.left + W + b.left) / 2} y={(a.top + b.top + H) / 2 - (e.when ? 18 : 6)} fill="#fbbf24" fontSize="13" textAnchor="middle">
                              ⚡
                            </text>
                          )}
                          {e.when &&
                            opacity >= 0.45 &&
                            (() => {
                              const m = midpoint(a.left + W, a.top + H / 2, b.left, b.top + H / 2);
                              const full = conditionText(e.when);
                              const text = full.length > 30 ? `${full.slice(0, 29)}…` : full;
                              const w = text.length * 6.3 + 18;
                              const otherwise = e.when.operator === "otherwise";
                              return (
                                <g
                                  data-fm-edge=""
                                  className="pointer-events-auto cursor-pointer"
                                  onPointerDown={(ev) => {
                                    ev.stopPropagation();
                                    setSelection({ edge: key });
                                  }}
                                >
                                  <title>{otherwise ? "Otherwise - when none of the other conditions holds" : `Only when ${full}`}</title>
                                  <rect x={m.x - w / 2} y={m.y - 10} width={w} height={20} rx={10} fill={otherwise ? "#1e293b" : "#2e1065"} stroke="#a78bfa" strokeWidth={1.2} />
                                  <text x={m.x} y={m.y + 4} fill="#ddd6fe" fontSize="11" fontStyle={otherwise ? "italic" : undefined} textAnchor="middle">
                                    {otherwise ? "otherwise" : `if ${text}`}
                                  </text>
                                </g>
                              );
                            })()}
                        </g>
                      );
                    })}
                    {link && byCode[link.from] && (
                      <path
                        d={path(place(byCode[link.from]).left + W, place(byCode[link.from]).top + H / 2, link.x, link.y)}
                        fill="none"
                        stroke="#38bdf8"
                        strokeWidth={2}
                        strokeDasharray="4 4"
                      />
                    )}
                  </svg>

                  {/* function points */}
                  {visibleNodes.map((n) => {
                    const p = place(n);
                    const selected = selectedNode?.code === n.code;
                    const dim = (process && !inProcess.has(n.code)) || (related && !related.has(n.code));
                    const changed = !isOriginal && compare && changedFromVanilla(n);
                    const procs = processesOf[n.code] ?? [];
                    const step = process ? steps.indexOf(n.code) + 1 : 0;
                    return (
                      <div
                        key={n.code}
                        data-fm-card=""
                        onPointerDown={(e) => startDrag(e, n)}
                        onPointerEnter={() => setHover(n.code)}
                        onPointerLeave={() => setHover((h) => (h === n.code ? null : h))}
                        onDoubleClick={() => frameAround(n.code)}
                        title={`${n.code} · ${n.description}\nDouble-click to frame it with its hand-offs`}
                        className={`group absolute flex items-center gap-2 rounded-lg border px-2.5 transition-[opacity,box-shadow] duration-150 ${
                          editable ? "cursor-grab active:cursor-grabbing" : "cursor-pointer"
                        } ${n.active ? "bg-slate-900" : "border-dashed bg-slate-950"} ${
                          selected
                            ? "border-sky-400 shadow-lg shadow-sky-950 ring-2 ring-sky-400/30"
                            : hitSet.has(n.code)
                              ? "border-amber-400 ring-2 ring-amber-400/40"
                              : changed
                                ? "border-violet-400/70"
                                : "border-slate-700 hover:border-slate-400"
                        }`}
                        style={{ left: p.left, top: p.top, width: W, height: H, opacity: dim ? 0.22 : n.active ? 1 : 0.55 }}
                      >
                        {step > 0 && (
                          <span
                            className="absolute -left-2.5 -top-2.5 flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10px] font-bold text-slate-950 shadow"
                            style={{ background: processColor[process] }}
                          >
                            {step}
                          </span>
                        )}
                        <span className="text-lg leading-none">{n.icon}</span>
                        <span className="min-w-0 flex-1">
                          <span className={`block truncate text-[12px] font-semibold ${n.active ? "text-slate-100" : "text-slate-500 line-through"}`}>{n.label}</span>
                          <span className="flex items-center gap-1 text-[10px] text-slate-500">
                            <span className="font-mono">{n.code}</span>
                            {n.segment && <span className={`rounded px-1 ${n.served === false ? "bg-slate-800 text-slate-600" : "bg-slate-800 text-slate-400"}`}>{n.segment}</span>}
                            {n.kind === "custom" && <span className="rounded bg-emerald-500/15 px-1 text-emerald-300">yours</span>}
                            {changed && <span className="rounded bg-violet-500/20 px-1 text-violet-300">changed</span>}
                            <span className="ml-auto flex gap-0.5">
                              {procs.slice(0, 4).map((k) => (
                                <span key={k} className="h-1.5 w-1.5 rounded-full" style={{ background: processColor[k] }} />
                              ))}
                            </span>
                          </span>
                        </span>
                        {editable && (
                          <span
                            onPointerDown={(e) => startLink(e, n)}
                            title="Drag to another function point to add a next step"
                            className="absolute -right-2 top-1/2 h-4 w-4 -translate-y-1/2 cursor-crosshair rounded-full border-2 border-slate-950 bg-sky-500 opacity-0 transition group-hover:opacity-100"
                          />
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* lane names - always on screen; click to fold an area away */}
                <div data-fm-ui="" className="pointer-events-none absolute inset-y-0 left-0 z-10" style={{ width: labelW }}>
                  {bands.map((b) => (
                    <button
                      key={b.key}
                      onClick={() => toggleLane(b.key)}
                      title={b.collapsed ? `Show ${b.label}` : `Fold ${b.label} away`}
                      className="pointer-events-auto absolute left-0 flex w-full items-center gap-1.5 overflow-hidden border-b border-r border-slate-800 bg-slate-950/90 px-2 text-left hover:bg-slate-900"
                      style={{ top: view.y + b.top * view.k, height: b.height * view.k }}
                    >
                      <span className="text-[10px] text-slate-500">{b.collapsed ? "▸" : "▾"}</span>
                      <span className="min-w-0">
                        <span className={`block truncate text-[12px] font-semibold ${b.collapsed ? "text-slate-500" : "text-slate-300"}`}>{b.label}</span>
                        {b.height * view.k > 40 && <span className="block truncate text-[10px] text-slate-600">{b.count} function points</span>}
                      </span>
                    </button>
                  ))}
                </div>

                <div data-fm-ui="">
                  <MiniMap
                    width={width}
                    height={height}
                    bands={bands}
                    nodes={visibleNodes}
                    laneTop={laneTop}
                    view={view}
                    size={size}
                    tint={nodeTint}
                    onMove={(cx, cy) => setView((v) => ({ ...v, x: size.w / 2 - cx * v.k, y: size.h / 2 - cy * v.k }))}
                  />
                </div>
                <p data-fm-ui="" className="pointer-events-none absolute bottom-3 z-10 hidden rounded-md bg-slate-950/80 px-2 py-1 text-[10px] text-slate-500 lg:block" style={{ left: labelW + 12 }}>
                  Drag the background to move · Ctrl + scroll to zoom · double-click a card to frame it · F fits · / finds
                </p>
              </>
            )}
          </div>

          {/* ---------- inspector ---------- */}
          <aside className={`w-full shrink-0 space-y-3 xl:w-80 xl:overflow-y-auto ${full ? "xl:h-full" : "xl:max-h-[72vh]"}`}>
            {selectedNode ? (
              <NodePanel
                key={selectedNode.code}
                node={selectedNode}
                editable={editable}
                lanes={map?.lanes ?? []}
                vanilla={vanillaNodes[selectedNode.code]}
                edges={shownEdges}
                names={byCode}
                all={sortedNodes}
                processes={(map?.processes ?? []).filter((p) => p.steps.includes(selectedNode.code))}
                processColor={processColor}
                onChange={(patch) => {
                  remember(`node:${selectedNode.code}:${Object.keys(patch).join(",")}`);
                  setNode(selectedNode.code, patch);
                }}
                onAdd={(target, kind) => addEdge(selectedNode.code, target, kind)}
                onSelectEdge={(key) => setSelection({ edge: key })}
                onJump={jumpTo}
                onProcess={pickProcess}
                onOpenIntents={onOpenIntents}
              />
            ) : selectedEdge ? (
              <EdgePanel
                edge={selectedEdge}
                editable={editable}
                names={byCode}
                onChange={(patch) => {
                  const key = edgeKey(selectedEdge);
                  const next = { ...selectedEdge, ...patch };
                  if (next.kind === "auto" && next.when?.operator === "otherwise") next.when = null; // an automation needs a real condition
                  if (patch.kind && edges.some((e) => edgeKey(e) === edgeKey(next))) return;
                  remember(`edge:${key}:${Object.keys(patch).join(",")}`);
                  setEdges((all) => all.map((e) => (edgeKey(e) === key ? { ...next, by_map: next.kind === "auto" ? true : e.by_map } : e)));
                  setSelection({ edge: edgeKey(next) });
                }}
                onRemove={() => removeEdge(selectedEdge)}
                siblings={shownEdges.filter((e) => e.source === selectedEdge.source && e.active !== false)}
                onJump={jumpTo}
                onOpenFlowStudio={onOpenFlowStudio}
              />
            ) : (
              <OverviewPanel map={map} editable={editable} lanes={map?.lanes ?? []} nodes={shownNodes} processColor={processColor} onProcess={pickProcess} onFocus={jumpTo} />
            )}
          </aside>
        </div>
      </div>

      {/* ---------- simulation ---------- */}
      {simulation && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" onClick={() => setSimulation(null)}>
          <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-slate-100">{simulation.ok ? "✓ The simulation passed" : "The simulation found something to look at"}</h3>
            <p className="mt-1 text-xs text-slate-400">
              Every process in this version was built and its automations played through on recent records - then everything was rolled back. Nothing was
              kept. {simulation.impact?.headline ?? ""}
            </p>
            {simulation.builds.map((b) => (
              <section key={b.name} className="mt-4">
                <h4 className="text-sm font-semibold text-slate-100">
                  {b.ok ? "✓" : "⚠"} {b.name}
                </h4>
                {b.message && <p className="text-xs text-amber-300">{b.message}</p>}
                <ul className="mt-1.5 space-y-1.5">
                  {b.checks.map((c) => (
                    <li key={c.component} className={`rounded-lg border px-3 py-2 text-xs ${c.ok ? "border-slate-800 bg-slate-950/60 text-slate-200" : "border-amber-500/30 bg-amber-500/10 text-amber-200"}`}>
                      <span className="font-medium">{c.title}</span> - {c.message}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {simulation.plan.problems.length > 0 && (
              <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-amber-200">
                {simulation.plan.problems.map((p, i) => (
                  <li key={i}>{p.text}</li>
                ))}
              </ul>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setSimulation(null)} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500">
                Close
              </button>
              {typeof showing === "number" && simulation.ok && (
                <button
                  onClick={() => {
                    setSimulation(null);
                    void review(showing);
                  }}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
                >
                  Activate…
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ---------- consent ---------- */}
      {plan && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4" onClick={() => !busy && setPlan(null)}>
          <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-slate-100">
              {planFor === "original" ? "Go back to BRT's original map?" : `Activate “${versions?.versions.find((v) => v.id === planFor)?.name ?? "this version"}”?`}
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              {plan.changes.length
                ? `${plan.changes.length} change(s) will be made in the engines, all together, and the business runs on it from then on. Nothing is deleted - switched-off steps and automations are kept and can be switched back on.`
                : "Only the layout differs - nothing changes in the engines."}{" "}
              {versions && !versions.on_original && "The version in use now is retired - kept, to activate again."}
            </p>
            {Object.entries(
              plan.changes.reduce<Record<string, MapChange[]>>((g, c) => {
                (g[c.engine] ??= []).push(c);
                return g;
              }, {}),
            ).map(([engine, list]) => (
              <section key={engine} className="mt-4">
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{engine}</h4>
                <ul className="mt-1.5 space-y-1.5">
                  {list.map((c, i) => (
                    <li key={i} className="rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-sm text-slate-200">
                      {c.text}
                      {c.warning && <p className="mt-1 text-xs text-amber-300">⚠ {c.warning}</p>}
                      {(c.pieces ?? []).length > 0 && (
                        <ul className="mt-1 list-disc pl-5 text-xs text-slate-400">
                          {c.pieces!.map((p) => (
                            <li key={p}>{p}</li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            ))}
            {plan.problems.length > 0 && (
              <section className="mt-4">
                <h4 className="text-[11px] font-semibold uppercase tracking-wider text-amber-400">Can't be done - left as it is</h4>
                <ul className="mt-1.5 space-y-1.5">
                  {plan.problems.map((p, i) => (
                    <li key={i} className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                      {p.text}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setPlan(null)} disabled={busy} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500">
                Not now
              </button>
              <button onClick={confirm} disabled={busy} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-40">
                {busy ? "Activating…" : plan.changes.length ? `Activate - ${plan.changes.length} change(s)` : "Activate"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- panels

const panel = "rounded-xl border border-slate-800 bg-slate-900/50 p-4";
const input = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none";

function NodePanel({
  node,
  editable,
  lanes,
  vanilla,
  edges,
  names,
  all,
  processes,
  processColor,
  onChange,
  onAdd,
  onSelectEdge,
  onJump,
  onProcess,
  onOpenIntents,
}: {
  node: MapNode;
  editable: boolean;
  lanes: FunctionMap["lanes"];
  vanilla?: MapNode;
  edges: MapEdge[];
  names: Record<string, MapNode>;
  all: MapNode[];
  processes: FunctionMap["processes"];
  processColor: Record<string, string>;
  onChange: (patch: Partial<MapNode>) => void;
  onAdd: (target: string, kind: MapEdge["kind"]) => void;
  onSelectEdge: (key: string) => void;
  onJump: (code: string) => void;
  onProcess: (key: string) => void;
  onOpenIntents?: () => void;
}) {
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<MapEdge["kind"]>("next");
  const out = edges.filter((e) => e.source === node.code && e.active !== false);
  const into = edges.filter((e) => e.target === node.code && e.active !== false);
  const row = (e: MapEdge, other: string, arrow: string) => (
    <li key={edgeKey(e)} className="group flex items-center rounded hover:bg-slate-800">
      <button onClick={() => onSelectEdge(edgeKey(e))} className="flex min-w-0 flex-1 items-center gap-1.5 px-1.5 py-1 text-left text-xs text-slate-300" title="Open this link">
        <span className="text-slate-500">{arrow}</span>
        <span>{names[other]?.icon}</span>
        <span className="min-w-0 flex-1 truncate">{names[other]?.label ?? other}</span>
        {e.when && <span className="max-w-[45%] shrink-0 truncate rounded bg-violet-500/15 px-1 text-[10px] text-violet-300">{conditionText(e.when)}</span>}
        {e.kind === "auto" && <span className="text-amber-300">⚡</span>}
      </button>
      <button onClick={() => onJump(other)} className="px-1.5 text-[11px] text-slate-500 opacity-0 hover:text-sky-300 group-hover:opacity-100" title="Go to this function point">
        Go →
      </button>
    </li>
  );
  return (
    <div className={panel}>
      <div className="flex items-start gap-2">
        <span className="text-2xl leading-none">{node.icon}</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-100">{node.label}</p>
          <p className="text-[11px] text-slate-500">
            <span className="font-mono text-sky-300">{node.code}</span>
            {node.target && ` · works on ${node.target.replace("@entity_type", "any record").replace("_", " ")}`}
            {node.segment && ` · ${node.segment} only`}
          </p>
        </div>
      </div>
      <p className="mt-2 text-xs leading-relaxed text-slate-400">{node.description}</p>
      {processes.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {processes.map((p) => (
            <button
              key={p.key}
              onClick={() => onProcess(p.key)}
              title="Highlight this process"
              className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-300 hover:border-slate-500"
            >
              <span className="mr-1 inline-block h-1.5 w-1.5 rounded-full align-middle" style={{ background: processColor[p.key] }} />
              {p.label} · step {p.steps.indexOf(node.code) + 1}
            </button>
          ))}
        </div>
      )}

      {editable ? (
        <div className="mt-4 space-y-3 border-t border-slate-800 pt-3">
          <label className="flex items-center justify-between gap-2 text-xs text-slate-300">
            <span>
              In use
              <span className="block text-[10px] text-slate-500">Off = nobody can run it (kept, greyed on the map)</span>
            </span>
            <button
              onClick={() => onChange({ active: !node.active })}
              className={`relative h-5 w-9 rounded-full transition ${node.active ? "bg-emerald-500" : "bg-slate-700"}`}
              aria-pressed={node.active}
            >
              <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition ${node.active ? "left-[18px]" : "left-0.5"}`} />
            </button>
          </label>
          <label className="block text-xs text-slate-400">
            Name
            <input value={node.label} onChange={(e) => onChange({ label: e.target.value })} className={input} />
            {vanilla && node.label !== vanilla.label && (
              <button onClick={() => onChange({ label: vanilla.label })} className="mt-1 text-[11px] text-sky-400 hover:text-sky-300">
                ↺ BRT's name: {vanilla.label}
              </button>
            )}
          </label>
          <label className="block text-xs text-slate-400">
            Business area (lane)
            <select value={node.lane} onChange={(e) => onChange({ lane: e.target.value })} className={input}>
              {lanes.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : (
        <p className="mt-3 text-[11px] text-slate-500">🔒 Read-only - change it in a draft version, then activate the version.</p>
      )}

      <div className="mt-4 grid gap-3 border-t border-slate-800 pt-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Comes after ({into.length})</p>
          <ul className="mt-1">{into.map((e) => row(e, e.source, "←"))}</ul>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Leads to ({out.length})</p>
          <ul className="mt-1">{out.map((e) => row(e, e.target, "→"))}</ul>
        </div>
        {editable && (
          <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-2">
            <p className="text-[11px] text-slate-400">Add a step after this</p>
            <FunctionPointPicker nodes={all.filter((candidate) => candidate.code !== node.code)} value={target} onChange={setTarget} />
            <div className="mt-1.5 flex gap-1.5">
              <select value={kind} onChange={(e) => setKind(e.target.value as MapEdge["kind"])} className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200">
                <option value="next">Suggest it next</option>
                <option value="auto">⚡ Run it automatically</option>
              </select>
              <button
                disabled={!target}
                onClick={() => {
                  onAdd(target, kind);
                  setTarget("");
                }}
                className="rounded-md bg-sky-600 px-3 py-1 text-xs font-semibold text-white disabled:opacity-40"
              >
                Add
              </button>
            </div>
          </div>
        )}
        {onOpenIntents && (
          <button onClick={onOpenIntents} className="justify-self-start text-[11px] text-sky-400 hover:text-sky-300">
            Fields, roles and phrases → Admin › Intents
          </button>
        )}
      </div>
    </div>
  );
}

function EdgePanel({
  edge,
  editable,
  names,
  onChange,
  onRemove,
  siblings,
  onJump,
  onOpenFlowStudio,
}: {
  edge: MapEdge;
  editable: boolean;
  names: Record<string, MapNode>;
  onChange: (patch: Partial<MapEdge>) => void;
  onRemove: () => void;
  siblings: MapEdge[];
  onJump: (code: string) => void;
  onOpenFlowStudio?: () => void;
}) {
  const a = names[edge.source];
  const b = names[edge.target];
  const locked = edge.kind === "auto" && edge.by_map === false;
  return (
    <div className={panel}>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        {edge.kind === "auto" ? "⚡ Automation" : "Next step"}
        {edge.when && <span className="ml-2 rounded bg-violet-500/20 px-1.5 py-0.5 normal-case tracking-normal text-violet-300">if {conditionText(edge.when)}</span>}
      </p>
      <p className="mt-1 text-sm text-slate-100">
        <button onClick={() => onJump(edge.source)} className="hover:text-sky-300" title="Go to this function point">
          {a?.icon} {a?.label}
        </button>{" "}
        <span className="text-slate-500">→</span>{" "}
        <button onClick={() => onJump(edge.target)} className="hover:text-sky-300" title="Go to this function point">
          {b?.icon} {b?.label}
        </button>
      </p>
      <p className="mt-2 text-xs text-slate-400">
        {edge.kind === "auto"
          ? `Whenever “${a?.label}” runs${edge.when ? ` and ${conditionText(edge.when)}` : ""}, “${b?.label}” runs straight after on the same record.`
          : edge.when?.operator === "otherwise"
            ? `After “${a?.label}”, “${b?.label}” is offered when none of the other conditions holds.`
            : `After “${a?.label}”, “${b?.label}” is offered under What's next${edge.when ? ` when ${conditionText(edge.when)}` : ""} - prefilled.`}
      </p>
      {!editable && edge.when && <p className="mt-1 text-xs text-violet-300">Condition: {conditionText(edge.when)}</p>}
      {edge.flow_name && <p className="mt-1 text-[11px] text-slate-500">Flow: {edge.flow_name}</p>}
      {locked ? (
        <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 p-2 text-xs text-slate-400">
          Made in Flow Studio - change or switch it off there.
          {onOpenFlowStudio && (
            <button onClick={onOpenFlowStudio} className="ml-1 text-sky-400 hover:text-sky-300">
              Open Flow Studio →
            </button>
          )}
        </div>
      ) : editable ? (
        <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
          <div className="flex rounded-lg border border-slate-700 p-0.5 text-xs">
            {(
              [
                ["next", "Suggest it next"],
                ["auto", "⚡ Run automatically"],
              ] as [MapEdge["kind"], string][]
            ).map(([k, label]) => (
              <button key={k} onClick={() => onChange({ kind: k })} className={`flex-1 rounded-md px-2 py-1 ${edge.kind === k ? "bg-sky-600 text-white" : "text-slate-400 hover:text-slate-200"}`}>
                {label}
              </button>
            ))}
          </div>
          <ConditionEditor edge={edge} source={a} siblings={siblings} names={names} editable={editable} onChange={(when) => onChange({ when })} />
          {edge.kind === "next" && (
            <label className="block text-xs text-slate-400">
              Why it's next (shown to the user)
              <input value={edge.why ?? ""} onChange={(e) => onChange({ why: e.target.value })} placeholder="e.g. Qualify the lead" className={input} />
            </label>
          )}
          <button onClick={onRemove} className="text-xs text-red-300 hover:text-red-200" title="Delete key also removes it">
            Remove this link
          </button>
        </div>
      ) : null}
    </div>
  );
}

function OverviewPanel({
  map,
  editable,
  lanes,
  nodes,
  processColor,
  onProcess,
  onFocus,
}: {
  map: FunctionMap | null;
  editable: boolean;
  lanes: FunctionMap["lanes"];
  nodes: MapNode[];
  processColor: Record<string, string>;
  onProcess: (key: string) => void;
  onFocus: (code: string) => void;
}) {
  const diffs = map?.vanilla ? [] : map?.differences ?? [];
  const off = nodes.filter((n) => !n.active).length;
  return (
    <>
      <div className={panel}>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">At a glance</p>
        <div className="mt-2 grid grid-cols-3 gap-2 text-center">
          {[
            [nodes.length, "function points"],
            [lanes.filter((l) => nodes.some((n) => n.lane === l.key)).length, "areas"],
            [off, "switched off"],
          ].map(([v, l]) => (
            <div key={String(l)} className="rounded-lg bg-slate-950/60 py-2">
              <p className="text-lg font-semibold text-slate-100">{v}</p>
              <p className="text-[10px] text-slate-500">{l}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Processes</p>
        <ul className="mt-1 space-y-0.5">
          {(map?.processes ?? []).map((p) => (
            <li key={p.key}>
              <button onClick={() => onProcess(p.key)} className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-xs text-slate-300 hover:bg-slate-800" title="Highlight this process">
                <span className="h-2 w-2 rounded-full" style={{ background: processColor[p.key] }} />
                <span className="flex-1">{p.label}</span>
                <span className="text-slate-600">{p.steps.length}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className={panel}>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Getting around</p>
        <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-slate-400">
          <li>Drag the background to move; Ctrl + scroll (or pinch) to zoom; Fit or F shows it all.</li>
          <li>Point at a card to light up its links; double-click it to frame it with its hand-offs.</li>
          <li>Pick a process to number its steps and frame them; fold away areas you don't need.</li>
          <li>Focus links draws only the links you are looking at.</li>
        </ul>
      </div>
      {editable && (
        <div className={panel}>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">How to change it</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-slate-400">
            <li>Drag a card to another lane to move it to that business area.</li>
            <li>Drag from the blue dot on a card's right edge to another card to add a next step.</li>
            <li>Click a link to make it run ⚡ automatically, reword it or remove it.</li>
            <li>Give a link a condition (Only when… / Otherwise) to branch: e.g. after Approve a quote, Send a proposal if the decision is approve, Revise the quote otherwise. Conditions show as purple pills.</li>
            <li>Click a card to rename it or switch it off.</li>
            <li>Tidy up lines the cards up by the order of the hand-offs; Undo (Ctrl+Z) takes back any change.</li>
            <li>Save draft keeps your work - nothing runs from a draft. Activate… shows every change in the engines before anything is made.</li>
          </ul>
        </div>
      )}
      {!editable && !map?.vanilla && (
        <div className={panel}>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">How to change it</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-slate-400">
            <li>This map is read-only. Make a new version from it (or from BRT's original), work on the draft, then activate it.</li>
            <li>The version it replaces is retired, not lost - activate it again to go back.</li>
            <li>BRT's releases reach every version: new hand-offs are added and retired ones dropped, without undoing your own changes.</li>
          </ul>
        </div>
      )}
      {diffs.length > 0 && (
        <div className={panel}>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Different from the original ({diffs.length})</p>
          <ul className="mt-1.5 max-h-72 space-y-1 overflow-y-auto">
            {diffs.map((d, i) => (
              <li key={i}>
                <button onClick={() => onFocus(d.code)} className="w-full rounded px-1.5 py-1 text-left text-xs text-slate-300 hover:bg-slate-800">
                  {d.text}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
