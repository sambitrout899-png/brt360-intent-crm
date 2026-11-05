/** Pane controls for every page: collapse, full view, width and height, plus
 * the page zoom. Pages need no changes - any bordered, rounded card inside the
 * main area whose first child is a heading (h1-h4, header, summary, or a row
 * starting with a heading) is a pane. `data-pane` forces a card in,
 * `data-pane="off"` keeps one out.
 *
 * State lives in data attributes and CSS variables on the pane element, which
 * React never manages, so re-renders do not undo it; sizes and collapsed
 * panes are remembered per page in this browser. */

import { useCallback, useEffect, useState } from "react";

/** c collapsed · h/w size · row full row width · d docked in the side rail */
export type PaneState = { c?: 1; h?: number; w?: number; row?: 1; d?: 1 };
type Saved = Record<string, PaneState>;

const HEADING = /^H[1-4]$/;
const CARD_TAGS = /^(SECTION|ARTICLE|DETAILS|DIV|ASIDE)$/;
const STORE = "brt.panes.";

export function paneRoot(): HTMLElement | null {
  return document.querySelector<HTMLElement>("[data-pane-root]");
}

/** The pane's title row - what stays visible when it is collapsed. */
export function headOf(el: Element): Element | null {
  const kids = el.children;
  for (let i = 0; i < Math.min(2, kids.length); i++) {
    const c = kids[i];
    if (c.hasAttribute("data-pk-ui")) continue;
    if (HEADING.test(c.tagName) || c.tagName === "HEADER" || c.tagName === "SUMMARY") return c;
    if (c.tagName === "DIV" && c.firstElementChild && (HEADING.test(c.firstElementChild.tagName) || c.firstElementChild.tagName === "DIV" && HEADING.test(c.firstElementChild.firstElementChild?.tagName ?? ""))) return c;
  }
  return null;
}

export function isPane(el: Element): el is HTMLElement {
  if (!(el instanceof HTMLElement)) return false;
  const flag = el.dataset.pane;
  if (flag === "off") return false;
  if (el.hasAttribute("data-pk-max") || el.hasAttribute("data-pk-dock")) return true;
  if (flag !== undefined) return !!headOf(el);
  if (!CARD_TAGS.test(el.tagName) || !headOf(el)) return false;
  const cs = getComputedStyle(el);
  // Zoomed pages report a 1px border as a fraction of a pixel - any border counts.
  if (cs.position === "fixed" || cs.borderTopStyle === "none" || !(parseFloat(cs.borderTopWidth) > 0) || !(parseFloat(cs.borderLeftWidth) > 0)) return false;
  if (parseFloat(cs.borderTopLeftRadius) < 4) return false;
  const r = el.getBoundingClientRect();
  return r.width >= 200 && r.height >= 44;
}

/** The innermost pane around a node, inside the page area. */
export function paneAt(node: Node | null, root: HTMLElement): HTMLElement | null {
  for (let el = node instanceof Element ? node : node?.parentElement ?? null; el && el !== root; el = el.parentElement) {
    if (el.closest("[role=dialog]") && !root.contains(el.closest("[role=dialog]"))) return null;
    if (isPane(el)) return el;
  }
  return null;
}

export function allPanes(root: HTMLElement): HTMLElement[] {
  const out: HTMLElement[] = [];
  root.querySelectorAll("[data-pane], section, article, details, aside, div[class*=border], div[class*=rounded]").forEach(el => {
    if (headOf(el) && isPane(el)) out.push(el as HTMLElement);
  });
  return out;
}

/** Stable enough across visits: title text and position among its siblings. */
export function paneId(el: Element): string {
  const head = headOf(el);
  const text = (head?.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 60);
  const index = el.parentElement ? Array.prototype.indexOf.call(el.parentElement.children, el) : 0;
  return `${text}|${index}`;
}

function load(page: string): Saved {
  try { return JSON.parse(localStorage.getItem(STORE + page) || "{}") as Saved; } catch { return {}; }
}
function store(page: string, saved: Saved) {
  try {
    if (Object.keys(saved).length) localStorage.setItem(STORE + page, JSON.stringify(saved));
    else localStorage.removeItem(STORE + page);
  } catch { /* storage unavailable - the layout just isn't remembered */ }
}

/** The page a pane belongs to - with two pages side by side, each remembers its own layout. */
export function pageOf(el: Element, fallback: string): string {
  return el.closest<HTMLElement>("[data-page]")?.dataset.page ?? fallback;
}

export function readState(el: HTMLElement): PaneState {
  const s: PaneState = {};
  if (el.hasAttribute("data-pk-collapsed")) s.c = 1;
  if (el.hasAttribute("data-pk-row")) s.row = 1;
  if (el.hasAttribute("data-pk-dock")) s.d = 1;
  const h = parseFloat(el.style.getPropertyValue("--pk-h"));
  const w = parseFloat(el.style.getPropertyValue("--pk-w"));
  if (h) s.h = h;
  if (w) s.w = w;
  return s;
}

export function applyState(el: HTMLElement, s: PaneState) {
  headOf(el)?.setAttribute("data-pk-head", "");
  el.toggleAttribute("data-pk-collapsed", !!s.c);
  el.toggleAttribute("data-pk-row", !!s.row);
  el.toggleAttribute("data-pk-dock", !!s.d);
  for (const [key, value] of [["h", s.h], ["w", s.w]] as const) {
    el.toggleAttribute(`data-pk-${key}`, !!value);
    if (value) el.style.setProperty(`--pk-${key}`, `${Math.round(value)}px`);
    else el.style.removeProperty(`--pk-${key}`);
  }
}

export function setPane(page: string, el: HTMLElement, patch: Partial<Record<keyof PaneState, unknown>>) {
  const next = { ...readState(el), ...patch } as PaneState;
  for (const k of Object.keys(next) as (keyof PaneState)[]) if (!next[k]) delete next[k];
  applyState(el, next);
  const key = pageOf(el, page);
  const saved = load(key);
  const id = paneId(el);
  if (Object.keys(next).length) saved[id] = next;
  else delete saved[id];
  store(key, saved);
  layoutDock();
}

/** Re-apply remembered layout after the page (re)renders. */
export function restore(page: string, root: HTMLElement) {
  const cache = new Map<string, Saved>();
  for (const el of allPanes(root)) {
    el.setAttribute("data-pk-pane", ""); // styled as a card (workspace.css)
    const key = pageOf(el, page);
    if (!cache.has(key)) cache.set(key, load(key));
    const s = cache.get(key)![paneId(el)];
    if (s) applyState(el, s);
  }
  layoutDock();
}

/** Stack the docked panes in the side rail: collapsed ones take their title row, the rest share the height. */
export function layoutDock(): number {
  const html = document.documentElement;
  const header = document.querySelector("[data-app-header]")?.getBoundingClientRect().bottom ?? 64;
  html.style.setProperty("--header-h", `${Math.round(header)}px`);
  const docked = Array.from(document.querySelectorAll<HTMLElement>("[data-pk-dock]")).filter((el) => !el.hasAttribute("data-pk-max"));
  html.toggleAttribute("data-docked", docked.length > 0);
  if (!docked.length) return 0;
  const gap = 10;
  let top = header + gap;
  const open = docked.filter((el) => !el.hasAttribute("data-pk-collapsed"));
  const closedHeight = docked.filter((el) => el.hasAttribute("data-pk-collapsed")).reduce((sum, el) => sum + el.getBoundingClientRect().height + gap, 0);
  const share = Math.max(140, (window.innerHeight - header - gap - closedHeight) / Math.max(1, open.length));
  for (const el of docked) {
    el.style.setProperty("--pk-dock-top", `${Math.round(top)}px`);
    const collapsed = el.hasAttribute("data-pk-collapsed");
    if (!collapsed) el.style.setProperty("--pk-dock-h", `${Math.round(share)}px`);
    top += collapsed ? el.getBoundingClientRect().height + gap : share;
  }
  return docked.length;
}

/** Collapse or expand every pane on the page (collapse: outermost panes only,
 * so a pane's inner cards are not folded away twice). */
export function setAll(page: string, root: HTMLElement, collapsed: boolean) {
  const panes = allPanes(root);
  for (const el of panes) {
    if (collapsed && panes.some(p => p !== el && p.contains(el))) continue;
    setPane(page, el, { c: collapsed ? 1 : undefined });
  }
}

export function resetAll(page: string, root: HTMLElement) {
  const pages = new Set([page]);
  for (const el of allPanes(root)) {
    applyState(el, {});
    pages.add(pageOf(el, page));
  }
  root.querySelectorAll("[data-pk-max]").forEach(el => el.removeAttribute("data-pk-max"));
  pages.forEach((p) => store(p, {}));
  layoutDock();
}

/** Return every docked pane to its page. */
export function undockAll(page: string, root: HTMLElement) {
  root.querySelectorAll<HTMLElement>("[data-pk-dock]").forEach((el) => setPane(page, el, { d: undefined }));
  layoutDock();
}

// ---------------------------------------------------------------- zoom

export const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
const ZOOM_KEY = "brt.zoom";

function readZoom(): number {
  try {
    const z = Number(localStorage.getItem(ZOOM_KEY));
    return ZOOM_STEPS.includes(z) ? z : 1;
  } catch { return 1; }
}

/** The page zoom (the top bar and menu stay at 100% so they stay reachable). */
export function useZoom() {
  const [zoom, setZoomState] = useState(readZoom);
  useEffect(() => {
    document.documentElement.style.setProperty("--app-zoom", String(zoom));
    try { if (zoom === 1) localStorage.removeItem(ZOOM_KEY); else localStorage.setItem(ZOOM_KEY, String(zoom)); } catch { /* not remembered */ }
  }, [zoom]);
  const step = useCallback((dir: -1 | 0 | 1) => setZoomState(z => {
    if (dir === 0) return 1;
    const i = ZOOM_STEPS.indexOf(z);
    return ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, (i < 0 ? 5 : i) + dir))];
  }), []);
  return { zoom, step };
}
