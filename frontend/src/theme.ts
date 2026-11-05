/** The application's colour themes (src/themes.css draws them; backend schemas/admin.APP_THEMES has the same list).
 *
 * The organization's platform administrator sets its theme (on creation or later); when the organization allows it,
 * each user may pick another for themselves (kept in this browser). "system" follows the device's light/dark setting. */

export type AppTheme = "dark" | "light" | "dim" | "system";
export type ResolvedTheme = "dark" | "light" | "dim";

export const THEMES: { value: AppTheme; label: string; hint: string }[] = [
  { value: "dark", label: "Midnight", hint: "Dark - deep navy surfaces, the classic look" },
  { value: "light", label: "Daylight", hint: "Light - white surfaces, best in bright offices and for printing screenshots" },
  { value: "dim", label: "Dusk", hint: "Soft dark - charcoal surfaces, easier on the eyes over long shifts" },
  { value: "system", label: "Match device", hint: "Automatic - light or dark, following each user's computer or phone setting" },
];

/** Accent palettes - the colour of buttons, links, highlights and the page's ambient glow. Each is a
 * Tailwind colour family standing in for "sky" (the app's accent) with a companion for gradients;
 * "brand" picks the family nearest the organization's brand colour. */
export type AppAccent = "sky" | "blue" | "indigo" | "violet" | "teal" | "emerald" | "rose" | "amber" | "brand";
export const ACCENTS: { value: AppAccent; label: string; swatch: [string, string] }[] = [
  { value: "sky", label: "Ocean", swatch: ["#0ea5e9", "#6366f1"] },
  { value: "blue", label: "Cobalt", swatch: ["#3b82f6", "#8b5cf6"] },
  { value: "indigo", label: "Indigo", swatch: ["#6366f1", "#d946ef"] },
  { value: "violet", label: "Amethyst", swatch: ["#8b5cf6", "#ec4899"] },
  { value: "teal", label: "Lagoon", swatch: ["#14b8a6", "#0ea5e9"] },
  { value: "emerald", label: "Emerald", swatch: ["#10b981", "#14b8a6"] },
  { value: "rose", label: "Ruby", swatch: ["#f43f5e", "#f97316"] },
  { value: "amber", label: "Saffron", swatch: ["#f59e0b", "#f43f5e"] },
  { value: "brand", label: "Brand colour", swatch: ["#64748b", "#94a3b8"] },
];
const isAccent = (v: unknown): v is AppAccent => ACCENTS.some((a) => a.value === v);

/** The Tailwind family closest in hue to a brand colour (greys fall back to Ocean). */
export function familyForColor(hex: string | null | undefined): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex ?? "");
  if (!m) return "sky";
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  // linear sRGB -> OKLab -> hue / chroma
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const mm = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const A = 1.9779984951 * l - 2.428592205 * mm + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * mm - 0.808675766 * s;
  if (Math.hypot(A, B) < 0.04) return "sky";
  const hue = ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360;
  let best = "sky";
  let gap = 999;
  for (const [family, h] of Object.entries(FAMILY_HUES)) {
    const d = Math.min(Math.abs(h - hue), 360 - Math.abs(h - hue));
    if (d < gap) [best, gap] = [family, d];
  }
  return best;
}
// the hue of each family's 500 shade (Tailwind v4 palette)
const FAMILY_HUES: Record<string, number> = {
  red: 25.3, orange: 47.6, amber: 70.1, yellow: 86, lime: 131.7, green: 149.6, emerald: 163.2, teal: 182.5, cyan: 215.2,
  sky: 237.3, blue: 259.8, indigo: 277.1, violet: 293.5, purple: 303.9, fuchsia: 322.2, pink: 354.3, rose: 16.4,
};

const ORG_KEY = "brt360.theme.org"; // the organization's theme, so the next visit (and the sign-in page) opens in it
const USER_KEY = "brt360.theme.user"; // this user's own choice, when the organization allows one

const isTheme = (v: unknown): v is AppTheme => THEMES.some((t) => t.value === v);
const read = (key: string): AppTheme | null => {
  try {
    const v = localStorage.getItem(key);
    return isTheme(v) ? v : null;
  } catch {
    return null;
  }
};
const write = (key: string, v: AppTheme | null) => {
  try {
    if (v) localStorage.setItem(key, v);
    else localStorage.removeItem(key);
  } catch {
    /* private window - the theme lasts for this visit */
  }
};

const media = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

export function resolveTheme(theme: AppTheme): ResolvedTheme {
  return theme === "system" ? (media?.matches ? "light" : "dark") : theme;
}

const CHOICE_KEY = "brt360.theme.choice"; // whether the organization allows that

let org: AppTheme = read(ORG_KEY) ?? "dark";
let userChoice = (() => {
  try {
    return localStorage.getItem(CHOICE_KEY) === "1";
  } catch {
    return false;
  }
})();
const listeners = new Set<() => void>();

/** The theme in force: the user's own (if allowed), else the organization's. */
export function activeTheme(): AppTheme {
  return (userChoice && read(USER_KEY)) || org;
}

const ACCENT_KEY = "brt360.accent.org";
const USER_ACCENT_KEY = "brt360.accent.user";
const BRAND_KEY = "brt360.accent.brand";
const readKey = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const writeKey = (key: string, v: string | null) => {
  try {
    if (v) localStorage.setItem(key, v);
    else localStorage.removeItem(key);
  } catch {
    /* private window */
  }
};
let orgAccent: AppAccent = (() => {
  const v = readKey(ACCENT_KEY);
  return isAccent(v) ? v : "sky";
})();
let brandColor: string | null = readKey(BRAND_KEY);

/** The accent in force: the user's own (if allowed), else the organization's. */
export function activeAccent(): AppAccent {
  const mine = readKey(USER_ACCENT_KEY);
  return userChoice && isAccent(mine) ? mine : orgAccent;
}

/** The Tailwind family that plays the accent. */
export function accentFamily(accent: AppAccent = activeAccent()): string {
  return accent === "brand" ? familyForColor(brandColor) : accent;
}

export function organizationAccent(): AppAccent {
  return orgAccent;
}

export function userAccent(): AppAccent | null {
  const v = readKey(USER_ACCENT_KEY);
  return isAccent(v) ? v : null;
}

export function setUserAccent(accent: AppAccent | null) {
  writeKey(USER_ACCENT_KEY, accent);
  paint();
}

export function brandFamily(): string {
  return familyForColor(brandColor);
}

function paint() {
  const resolved = resolveTheme(activeTheme());
  const html = document.documentElement;
  html.dataset.theme = resolved;
  html.dataset.accent = accentFamily();
  const bg = resolved === "light" ? "#f3f5f9" : resolved === "dim" ? "#14181f" : "#070b14";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", bg);
  listeners.forEach((l) => l());
}

media?.addEventListener?.("change", () => activeTheme() === "system" && paint());

/** Called at start-up (last known theme) and when the organization's branding arrives. */
export function applyOrganizationTheme(theme?: AppTheme | null, allowUserChoice?: boolean, accent?: AppAccent | null, brand?: string | null) {
  if (theme !== undefined) {
    org = isTheme(theme) ? theme : "dark";
    write(ORG_KEY, org);
  }
  if (accent !== undefined) {
    orgAccent = isAccent(accent) ? accent : "sky";
    writeKey(ACCENT_KEY, orgAccent);
  }
  if (brand !== undefined) {
    brandColor = brand;
    writeKey(BRAND_KEY, brand);
  }
  if (allowUserChoice !== undefined) {
    userChoice = allowUserChoice;
    try {
      localStorage.setItem(CHOICE_KEY, allowUserChoice ? "1" : "0");
    } catch {
      /* private window */
    }
  }
  paint();
}

/** Whether this organization lets its users choose their own theme. */
export function userMayChoose(): boolean {
  return userChoice;
}

/** This user's own theme (null = back to the organization's). */
export function setUserTheme(theme: AppTheme | null) {
  write(USER_KEY, theme);
  paint();
}

export function userTheme(): AppTheme | null {
  return read(USER_KEY);
}

export function organizationTheme(): AppTheme {
  return org;
}

/** Re-render on a theme change (React: useSyncExternalStore(subscribeTheme, currentResolvedTheme)). */
export function subscribeTheme(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function currentResolvedTheme(): ResolvedTheme {
  return resolveTheme(activeTheme());
}
