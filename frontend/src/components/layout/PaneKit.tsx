import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { DENSITIES, setLayout, useLayout } from "../../layoutPrefs";
import { applyState, layoutDock, paneAt, paneRoot, readState, resetAll, restore, setAll, setPane, undockAll, useZoom } from "./paneDom";
import "./paneKit.css";

/** The Layout menu (top bar) and the controls that appear on whichever section the pointer or keyboard
 * focus is in: collapse, dock beside the page, full-row width, full view, reset, and drag handles for
 * width and height. Docked sections stack in a rail on the right whose width is dragged from its edge. */

type Rect = { top: number; left: number; width: number; height: number };
const rectOf = (el: HTMLElement): Rect => {
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
};
const headerBottom = () => document.querySelector("[data-app-header]")?.getBoundingClientRect().bottom ?? 64;

const ICON = {
  collapse: <path d="M5 7l5 6 5-6" />,
  expand: <path d="M7 5l6 5-6 5" />,
  row: <path d="M3 10h14M6 7l-3 3 3 3M14 7l3 3-3 3" />,
  reset: <path d="M4 10a6 6 0 1 0 2-4.5M4 4v3h3" />,
  max: <path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5" />,
  unmax: <path d="M8 3v5H3M12 3v5h5M8 17v-5H3M12 17v-5h5" />,
  dock: <><rect x="3" y="4" width="14" height="12" rx="1.5" /><path d="M12 4v12M14 8h1M14 11h1" /></>,
  undock: <><rect x="3" y="4" width="14" height="12" rx="1.5" /><path d="M9 7l-3 3 3 3M6 10h6" /></>,
};
const Svg = ({ d }: { d: React.ReactNode }) => <svg viewBox="0 0 20 20" aria-hidden="true">{d}</svg>;

export default function PaneKit({ page, pages = [] }: { page: string; pages?: { id: string; label: string }[] }) {
  const { zoom, step } = useZoom();
  const layout = useLayout();
  const [pane, setPaneEl] = useState<HTMLElement | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [maxEl, setMaxEl] = useState<HTMLElement | null>(null);
  const [menu, setMenu] = useState(false);
  const [docked, setDocked] = useState(0);
  const [, bump] = useState(0);
  const leaveTimer = useRef<number | undefined>(undefined);
  const dragging = useRef(false);

  const track = useCallback((el: HTMLElement | null) => {
    window.clearTimeout(leaveTimer.current);
    setPaneEl(el);
    setRect(el ? rectOf(el) : null);
  }, []);

  // Which pane the pointer / focus is in.
  useEffect(() => {
    let frame = 0;
    const find = (target: EventTarget | null) => {
      if (dragging.current) return;
      const node = target as Element | null;
      if (node?.closest?.("[data-pk-ui]")) { window.clearTimeout(leaveTimer.current); return; }
      // In full view the controls stay on the enlarged pane.
      const maxed = document.querySelector<HTMLElement>("[data-pk-max]");
      if (maxed) { track(maxed); return; }
      const root = paneRoot();
      const found = root && node && root.contains(node) ? paneAt(node, root) : null;
      if (found) track(found);
      else {
        window.clearTimeout(leaveTimer.current);
        leaveTimer.current = window.setTimeout(() => track(null), 350);
      }
    };
    const onMove = (e: PointerEvent) => {
      if (e.pointerType === "touch") return;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => find(e.target));
    };
    const onFocus = (e: FocusEvent) => find(e.target);
    document.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("focusin", onFocus);
    return () => { document.removeEventListener("pointermove", onMove); document.removeEventListener("focusin", onFocus); cancelAnimationFrame(frame); };
  }, [track]);

  // Keep the controls on the pane while the page scrolls or resizes.
  useEffect(() => {
    if (!pane) return;
    const update = () => setRect(pane.isConnected ? rectOf(pane) : null);
    window.addEventListener("scroll", update, { capture: true, passive: true });
    window.addEventListener("resize", update);
    const ro = new ResizeObserver(update);
    ro.observe(pane);
    return () => { window.removeEventListener("scroll", update, { capture: true }); window.removeEventListener("resize", update); ro.disconnect(); };
  }, [pane]);

  // Re-apply this page's remembered layout as its content renders, and re-stack the dock.
  useEffect(() => {
    let timer = 0;
    const run = () => { const root = paneRoot(); if (root) restore(page, root); setDocked(layoutDock()); };
    const mo = new MutationObserver(() => { window.clearTimeout(timer); timer = window.setTimeout(run, 250); });
    mo.observe(document.body, { childList: true, subtree: true });
    const onResize = () => setDocked(layoutDock());
    window.addEventListener("resize", onResize);
    run();
    return () => { mo.disconnect(); window.clearTimeout(timer); window.removeEventListener("resize", onResize); };
  }, [page]);
  // the density changes the top bar's height and the panes' title rows
  useEffect(() => { requestAnimationFrame(() => setDocked(layoutDock())); }, [layout.density, layout.dockWidth, zoom]);

  // Leaving a page ends full view.
  useEffect(() => () => { document.querySelectorAll("[data-pk-max]").forEach(el => el.removeAttribute("data-pk-max")); setMaxEl(null); }, [page]);

  const exitMax = useCallback(() => {
    maxEl?.removeAttribute("data-pk-max");
    setMaxEl(null);
    setDocked(layoutDock());
    if (maxEl) requestAnimationFrame(() => maxEl.scrollIntoView({ block: "nearest" }));
  }, [maxEl]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && maxEl) { e.preventDefault(); exitMax(); }
      else if (e.key === "Escape" && menu) setMenu(false);
      else if (e.ctrlKey && e.altKey && (e.key === "=" || e.key === "+")) { e.preventDefault(); step(1); }
      else if (e.ctrlKey && e.altKey && e.key === "-") { e.preventDefault(); step(-1); }
      else if (e.ctrlKey && e.altKey && e.key === "0") { e.preventDefault(); step(0); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maxEl, exitMax, step, menu]);

  const refresh = () => { bump(n => n + 1); setDocked(layoutDock()); if (pane) requestAnimationFrame(() => pane.isConnected && setRect(rectOf(pane))); };
  const act = (patch: Parameters<typeof setPane>[2]) => { if (pane) { setPane(page, pane, patch); refresh(); } };
  const toggleMax = () => {
    if (!pane) return;
    if (maxEl === pane) return exitMax();
    maxEl?.removeAttribute("data-pk-max");
    pane.setAttribute("data-pk-max", "");
    applyState(pane, { ...readState(pane), c: undefined });
    pane.removeAttribute("data-pk-collapsed");
    setMaxEl(pane);
    requestAnimationFrame(refresh);
  };

  const drag = (axis: "x" | "y" | "xy") => (e: React.PointerEvent) => {
    if (!pane || e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget as HTMLElement;
    handle.setPointerCapture(e.pointerId);
    dragging.current = true;
    const start = { x: e.clientX, y: e.clientY, w: pane.getBoundingClientRect().width / zoom, h: pane.getBoundingClientRect().height / zoom };
    const parent = pane.parentElement;
    const inGrid = parent ? getComputedStyle(parent).display.includes("grid") : false;
    const startState = readState(pane);
    let last = startState;
    const move = (ev: PointerEvent) => {
      const next = { ...startState };
      if (axis !== "y") {
        next.w = Math.max(200, start.w + (ev.clientX - start.x) / zoom);
        if (inGrid && next.w > start.w + 24) next.row = 1;
      }
      if (axis !== "x") next.h = Math.max(80, start.h + (ev.clientY - start.y) / zoom);
      applyState(pane, next);
      last = next;
      setRect(rectOf(pane));
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      dragging.current = false;
      setPane(page, pane, last);
      refresh();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  };

  // the dock rail's width: drag its left edge
  const dragRail = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const handle = e.currentTarget as HTMLElement;
    handle.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent) => setLayout({ dockWidth: window.innerWidth - ev.clientX });
    const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); setDocked(layoutDock()); };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  };

  const state = pane ? readState(pane) : {};
  const isMax = !!pane && pane === maxEl;
  const collapsed = !!state.c;
  const isDocked = !!state.d;
  const sized = !!(state.h || state.w || state.row);
  // Sits on the pane's top border, so it never hides the pane's own header buttons.
  const top = rect ? (isMax ? rect.top + 6 : Math.min(Math.max(rect.top - 15, headerBottom() + 2), rect.top + rect.height - 30)) : 0;
  const right = rect ? Math.max(4, window.innerWidth - (rect.left + rect.width) + 12) : 0;

  const root = () => paneRoot();
  const all = (collapse: boolean) => { const r = root(); if (r) setAll(page, r, collapse); setMenu(false); refresh(); };
  const others = pages.filter((p) => p.id !== page);
  const railTop = headerBottom();

  return <>
    <div className="pk-view" data-pk-ui>
      <button type="button" className="pk-view-button" aria-expanded={menu} aria-haspopup="menu" onClick={() => setMenu(!menu)} title="Layout: density, width, pages side by side, docked sections, zoom">
        <Svg d={<><rect x="3" y="3" width="6" height="14" rx="1.2" /><rect x="11" y="3" width="6" height="6" rx="1.2" /><rect x="11" y="11" width="6" height="6" rx="1.2" /></>} />
        <span>Layout</span>
        {(docked > 0 || layout.split) && <span className="pk-badge">{docked + (layout.split ? 1 : 0)}</span>}
      </button>
      {menu && <>
        <div className="pk-menu-scrim" onClick={() => setMenu(false)} />
        <div className="pk-menu" role="menu">
          <p className="pk-menu-label">Density</p>
          <div className="pk-seg" role="radiogroup" aria-label="Density">
            {DENSITIES.map((d) => (
              <button key={d.value} type="button" role="radio" aria-checked={layout.density === d.value} title={d.hint} onClick={() => setLayout({ density: d.value })}>{d.label}</button>
            ))}
          </div>
          <p className="pk-menu-label">Page width</p>
          <div className="pk-seg" role="radiogroup" aria-label="Page width">
            <button type="button" role="radio" aria-checked={layout.width === "contained"} onClick={() => setLayout({ width: "contained" })} title="A comfortable reading width, centred">Centred</button>
            <button type="button" role="radio" aria-checked={layout.width === "full"} onClick={() => setLayout({ width: "full" })} title="Use the whole screen">Full width</button>
          </div>

          <p className="pk-menu-label">Side by side</p>
          <select className="pk-select" value={layout.split ?? ""} onChange={(e) => { setLayout({ split: e.target.value || null }); setMenu(false); }} aria-label="Open a page beside this one">
            <option value="">One page</option>
            {others.map((p) => <option key={p.id} value={p.id}>Beside: {p.label}</option>)}
          </select>

          <p className="pk-menu-label">Sections on this page</p>
          <button type="button" role="menuitem" onClick={() => all(true)}>Collapse all</button>
          <button type="button" role="menuitem" onClick={() => all(false)}>Expand all</button>
          {docked > 0 && <button type="button" role="menuitem" onClick={() => { const r = root(); if (r) undockAll(page, r); setMenu(false); refresh(); }}>Return docked sections ({docked})</button>}
          <button type="button" role="menuitem" onClick={() => { const r = root(); if (r) resetAll(page, r); setMaxEl(null); setMenu(false); refresh(); }}>Reset this page's layout</button>

          <p className="pk-menu-label">Zoom</p>
          <div className="pk-zoom">
            <button type="button" onClick={() => step(-1)} disabled={zoom <= 0.5} aria-label="Zoom out" title="Zoom out (Ctrl+Alt+−)">−</button>
            <button type="button" onClick={() => step(0)} title="Reset to 100% (Ctrl+Alt+0)">{Math.round(zoom * 100)}%</button>
            <button type="button" onClick={() => step(1)} disabled={zoom >= 2} aria-label="Zoom in" title="Zoom in (Ctrl+Alt+=)">+</button>
          </div>
          <p className="pk-menu-hint">Point at any section for its controls: <b>dock</b> it to the right to keep it in view, collapse it, widen it to the full row, open it full screen, or drag its right or bottom edge. Your layout is remembered per page.</p>
        </div>
      </>}
    </div>

    {docked > 0 && createPortal(<>
      <div className="pk-rail" style={{ top: railTop }} aria-hidden="true" />
      <div className="pk-rail-grip" data-pk-ui style={{ top: railTop, right: `calc(var(--dock-w) - 6px)` }} onPointerDown={dragRail}
        onDoubleClick={() => { setLayout({ dockWidth: 420 }); }} title="Drag to widen or narrow the dock · double-click to reset" />
    </>, document.body)}

    {maxEl && createPortal(<div className="pk-scrim" data-pk-ui onClick={exitMax} />, document.body)}

    {pane && rect && createPortal(
      <div data-pk-ui>
        <div className="pk-toolbar" style={{ top, right }} role="toolbar" aria-label="Section controls">
          <button type="button" onClick={() => act({ c: collapsed ? undefined : 1 })} disabled={isMax} title={collapsed ? "Expand" : "Collapse"} aria-label={collapsed ? "Expand section" : "Collapse section"}>
            <Svg d={collapsed ? ICON.expand : ICON.collapse} />
          </button>
          <button type="button" onClick={() => act({ d: isDocked ? undefined : 1, w: undefined, h: undefined, row: undefined })} disabled={isMax} aria-pressed={isDocked}
            title={isDocked ? "Return to the page" : "Dock on the right - keep it in view beside the page"} aria-label={isDocked ? "Undock section" : "Dock section"}>
            <Svg d={isDocked ? ICON.undock : ICON.dock} />
          </button>
          {!isDocked && <button type="button" onClick={() => act({ row: state.row ? undefined : 1 })} disabled={isMax || collapsed} aria-pressed={!!state.row} title={state.row ? "Normal width" : "Full row width"} aria-label="Toggle full row width">
            <Svg d={ICON.row} />
          </button>}
          {sized && !isMax && !isDocked && <button type="button" onClick={() => act({ h: undefined, w: undefined, row: undefined })} title="Reset size" aria-label="Reset section size">
            <Svg d={ICON.reset} />
          </button>}
          <button type="button" onClick={toggleMax} title={isMax ? "Exit full view (Esc)" : "Full view"} aria-label={isMax ? "Exit full view" : "Full view"}>
            <Svg d={isMax ? ICON.unmax : ICON.max} />
          </button>
        </div>
        {!collapsed && !isMax && !isDocked && <>
          <div className="pk-handle pk-handle-x" style={{ top: rect.top + 12, left: rect.left + rect.width - 4, height: Math.max(0, rect.height - 24) }} onPointerDown={drag("x")}
            onDoubleClick={() => act({ w: undefined, row: undefined })} title="Drag to change width · double-click to reset" />
          <div className="pk-handle pk-handle-y" style={{ top: rect.top + rect.height - 4, left: rect.left + 12, width: Math.max(0, rect.width - 24) }} onPointerDown={drag("y")}
            onDoubleClick={() => act({ h: undefined })} title="Drag to change height · double-click to reset" />
          <div className="pk-handle pk-handle-xy" style={{ top: rect.top + rect.height - 10, left: rect.left + rect.width - 10 }} onPointerDown={drag("xy")} title="Drag to resize" />
        </>}
      </div>,
      document.body,
    )}
  </>;
}
