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

function paint() {
  const resolved = resolveTheme(activeTheme());
  const html = document.documentElement;
  html.dataset.theme = resolved;
  const bg = resolved === "light" ? "#f3f5f9" : resolved === "dim" ? "#14181f" : "#070b14";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", bg);
  listeners.forEach((l) => l());
}

media?.addEventListener?.("change", () => activeTheme() === "system" && paint());

/** Called at start-up (last known theme) and when the organization's branding arrives. */
export function applyOrganizationTheme(theme?: AppTheme | null, allowUserChoice?: boolean) {
  if (theme !== undefined) {
    org = isTheme(theme) ? theme : "dark";
    write(ORG_KEY, org);
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
