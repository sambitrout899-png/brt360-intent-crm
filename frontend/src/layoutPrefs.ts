/** How this user lays out the workspace (kept in this browser): density, page width, a second page
 * docked beside the first, and the width of the section dock. Applied as attributes on <html>
 * (src/workspace.css styles them); components re-render through subscribeLayout. */

import { useSyncExternalStore } from "react";

export type Density = "compact" | "comfortable" | "spacious";
export type PageWidth = "contained" | "full";

export type LayoutPrefs = {
  density: Density;
  width: PageWidth;
  /** the page docked beside the current one (null = one page) and its share of the width */
  split: string | null;
  splitRatio: number;
  /** the section dock's width in px */
  dockWidth: number;
  /** menu groups the user folded away */
  foldedGroups: string[];
};

export const DENSITIES: { value: Density; label: string; hint: string }[] = [
  { value: "compact", label: "Compact", hint: "More on screen - tighter spacing and smaller text" },
  { value: "comfortable", label: "Comfortable", hint: "The standard spacing" },
  { value: "spacious", label: "Spacious", hint: "Roomier - larger touch targets, easier reading" },
];

const KEY = "brt360.layout";
const DEFAULTS: LayoutPrefs = { density: "comfortable", width: "contained", split: null, splitRatio: 0.5, dockWidth: 420, foldedGroups: [] };

function load(): LayoutPrefs {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}") as Partial<LayoutPrefs>;
    return { ...DEFAULTS, ...saved };
  } catch {
    return { ...DEFAULTS };
  }
}

let prefs = load();
const listeners = new Set<() => void>();

function apply() {
  const html = document.documentElement;
  html.dataset.density = prefs.density;
  html.dataset.width = prefs.width;
  html.style.setProperty("--dock-w", `${Math.round(prefs.dockWidth)}px`);
}

export function layoutPrefs(): LayoutPrefs {
  return prefs;
}

export function setLayout(patch: Partial<LayoutPrefs>) {
  prefs = { ...prefs, ...patch };
  prefs.splitRatio = Math.min(0.8, Math.max(0.2, prefs.splitRatio));
  prefs.dockWidth = Math.min(Math.max(280, prefs.dockWidth), Math.round(window.innerWidth * 0.6));
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    /* private window - lasts for this visit */
  }
  apply();
  listeners.forEach((l) => l());
}

export function subscribeLayout(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useLayout(): LayoutPrefs {
  return useSyncExternalStore(subscribeLayout, layoutPrefs);
}

export function toggleGroup(group: string) {
  const folded = prefs.foldedGroups.includes(group) ? prefs.foldedGroups.filter((g) => g !== group) : [...prefs.foldedGroups, group];
  setLayout({ foldedGroups: folded });
}

apply();
