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

export type PaneState = { c?: 1; h?: number; w?: number; row?: 1 };
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
  if (el.hasAttribute("data-pk-max")) return true;
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

export function readState(el: HTMLElement): PaneState {
  const s: PaneState = {};
  if (el.hasAttribute("data-pk-collapsed")) s.c = 1;
  if (el.hasAttribute("data-pk-row")) s.row = 1;
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
  const saved = load(page);
  const id = paneId(el);
  if (Object.keys(next).length) saved[id] = next;
  else delete saved[id];
  store(page, saved);
}

/** Re-apply remembered layout after the page (re)renders. */
export function restore(page: string, root: HTMLElement) {
  const saved = load(page);
  if (!Object.keys(saved).length) return;
  for (const el of allPanes(root)) {
    const s = saved[paneId(el)];
    if (s) applyState(el, s);
  }
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
  for (const el of allPanes(root)) applyState(el, {});
  root.querySelectorAll("[data-pk-max]").forEach(el => el.removeAttribute("data-pk-max"));
  store(page, {});
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
