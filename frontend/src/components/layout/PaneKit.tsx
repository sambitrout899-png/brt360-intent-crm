import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { applyState, paneAt, paneRoot, readState, resetAll, restore, setAll, setPane, useZoom } from "./paneDom";
import "./paneKit.css";

/** The View menu (top bar) and the controls that appear on whichever pane the
 * pointer or keyboard focus is in: collapse, full view, full-row width,
 * reset, and drag handles for width and height. */

type Rect = { top: number; left: number; width: number; height: number };
const rectOf = (el: HTMLElement): Rect => {
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
};
const HEADER = 68; // the sticky top bar

export default function PaneKit({ page }: { page: string }) {
  const { zoom, step } = useZoom();
  const [pane, setPaneEl] = useState<HTMLElement | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [maxEl, setMaxEl] = useState<HTMLElement | null>(null);
  const [menu, setMenu] = useState(false);
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

  // Re-apply this page's remembered layout as its content renders.
  useEffect(() => {
    let timer = 0;
    const run = () => { const root = paneRoot(); if (root) restore(page, root); };
    const mo = new MutationObserver(() => { window.clearTimeout(timer); timer = window.setTimeout(run, 300); });
    mo.observe(document.body, { childList: true, subtree: true });
    run();
    return () => { mo.disconnect(); window.clearTimeout(timer); };
  }, [page]);

  // Leaving a page ends full view.
  useEffect(() => () => { document.querySelectorAll("[data-pk-max]").forEach(el => el.removeAttribute("data-pk-max")); setMaxEl(null); }, [page]);

  const exitMax = useCallback(() => {
    maxEl?.removeAttribute("data-pk-max");
    setMaxEl(null);
    if (maxEl) requestAnimationFrame(() => maxEl.scrollIntoView({ block: "nearest" }));
  }, [maxEl]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && maxEl) { e.preventDefault(); exitMax(); }
      else if (e.ctrlKey && e.altKey && (e.key === "=" || e.key === "+")) { e.preventDefault(); step(1); }
      else if (e.ctrlKey && e.altKey && e.key === "-") { e.preventDefault(); step(-1); }
      else if (e.ctrlKey && e.altKey && e.key === "0") { e.preventDefault(); step(0); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maxEl, exitMax, step]);

  const refresh = () => { bump(n => n + 1); if (pane) setRect(rectOf(pane)); };
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

  const state = pane ? readState(pane) : {};
  const isMax = !!pane && pane === maxEl;
  const collapsed = !!state.c;
  const sized = !!(state.h || state.w || state.row);
  // Sits on the pane's top border, so it never hides the pane's own header buttons.
  const top = rect ? (isMax ? rect.top + 6 : Math.min(Math.max(rect.top - 15, HEADER + 2), rect.top + rect.height - 30)) : 0;
  const right = rect ? Math.max(4, window.innerWidth - (rect.left + rect.width) + 12) : 0;

  const root = () => paneRoot();
  const all = (collapse: boolean) => { const r = root(); if (r) setAll(page, r, collapse); setMenu(false); refresh(); };

  return <>
    <div className="pk-view" data-pk-ui>
      <button type="button" className="pk-view-button" aria-expanded={menu} aria-haspopup="menu" onClick={() => setMenu(!menu)} title="Page view: zoom and panes">
        <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="3" width="6" height="6" rx="1.2" /><rect x="11" y="3" width="6" height="6" rx="1.2" /><rect x="3" y="11" width="14" height="6" rx="1.2" /></svg>
        <span>{Math.round(zoom * 100)}%</span>
      </button>
      {menu && <>
        <div className="pk-menu-scrim" onClick={() => setMenu(false)} />
        <div className="pk-menu" role="menu">
          <p className="pk-menu-label">Zoom</p>
          <div className="pk-zoom">
            <button type="button" onClick={() => step(-1)} disabled={zoom <= 0.5} aria-label="Zoom out" title="Zoom out (Ctrl+Alt+−)">−</button>
            <button type="button" onClick={() => step(0)} title="Reset to 100% (Ctrl+Alt+0)">{Math.round(zoom * 100)}%</button>
            <button type="button" onClick={() => step(1)} disabled={zoom >= 2} aria-label="Zoom in" title="Zoom in (Ctrl+Alt+=)">+</button>
          </div>
          <p className="pk-menu-label">Panes on this page</p>
          <button type="button" role="menuitem" onClick={() => all(true)}>Collapse all panes</button>
          <button type="button" role="menuitem" onClick={() => all(false)}>Expand all panes</button>
          <button type="button" role="menuitem" onClick={() => { const r = root(); if (r) resetAll(page, r); setMaxEl(null); setMenu(false); refresh(); }}>Reset pane layout</button>
          <p className="pk-menu-hint">Point at any pane for its controls: collapse, full view, full width, and drag its right or bottom edge to resize. Esc leaves full view.</p>
        </div>
      </>}
    </div>

    {maxEl && createPortal(<div className="pk-scrim" data-pk-ui onClick={exitMax} />, document.body)}

    {pane && rect && createPortal(
      <div data-pk-ui>
        <div className="pk-toolbar" style={{ top, right }} role="toolbar" aria-label="Pane controls">
          <button type="button" onClick={() => act({ c: collapsed ? undefined : 1 })} disabled={isMax} title={collapsed ? "Expand" : "Collapse"} aria-label={collapsed ? "Expand pane" : "Collapse pane"}>
            <svg viewBox="0 0 20 20" aria-hidden="true">{collapsed ? <path d="M7 5l6 5-6 5" /> : <path d="M5 7l5 6 5-6" />}</svg>
          </button>
          <button type="button" onClick={() => act({ row: state.row ? undefined : 1 })} disabled={isMax || collapsed} aria-pressed={!!state.row} title={state.row ? "Normal width" : "Full row width"} aria-label="Toggle full row width">
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 10h14M6 7l-3 3 3 3M14 7l3 3-3 3" /></svg>
          </button>
          {sized && !isMax && <button type="button" onClick={() => act({ h: undefined, w: undefined, row: undefined })} title="Reset size" aria-label="Reset pane size">
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 10a6 6 0 1 0 2-4.5M4 4v3h3" /></svg>
          </button>}
          <button type="button" onClick={toggleMax} title={isMax ? "Exit full view (Esc)" : "Full view"} aria-label={isMax ? "Exit full view" : "Full view"}>
            <svg viewBox="0 0 20 20" aria-hidden="true">{isMax ? <path d="M8 3v5H3M12 3v5h5M8 17v-5H3M12 17v-5h5" /> : <path d="M3 8V3h5M17 8V3h-5M3 12v5h5M17 12v5h-5" />}</svg>
          </button>
        </div>
        {!collapsed && !isMax && <>
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
