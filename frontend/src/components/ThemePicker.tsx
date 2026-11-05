import { useState, useSyncExternalStore } from "react";
import { THEMES, currentResolvedTheme, organizationTheme, setUserTheme, subscribeTheme, userMayChoose, userTheme } from "../theme";
import type { AppTheme } from "../theme";

/** A small live picture of the application in one theme - drawn with the real theme (src/themes.css), not a screenshot. */
function Miniature({ theme }: { theme: "dark" | "light" | "dim" }) {
  return (
    <div data-theme={theme} className="flex h-full w-full overflow-hidden bg-[var(--app-bg)]">
      <div className="flex w-1/4 flex-col gap-1 border-r border-slate-800 bg-[var(--app-panel)] p-1.5">
        <span className="h-1.5 w-3/4 rounded-sm bg-slate-700" />
        <span className="h-1.5 w-full rounded-sm bg-sky-500/20" />
        <span className="h-1.5 w-2/3 rounded-sm bg-slate-800" />
        <span className="h-1.5 w-3/4 rounded-sm bg-slate-800" />
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-1.5">
        <span className="h-1.5 w-1/2 rounded-sm bg-slate-300" />
        <div className="flex gap-1.5">
          <span className="h-6 flex-1 rounded border border-slate-800 bg-slate-900" />
          <span className="h-6 flex-1 rounded border border-slate-800 bg-slate-900" />
        </div>
        <span className="flex-1 rounded border border-slate-800 bg-slate-900" />
        <span className="h-2 w-8 self-end rounded-sm bg-sky-600" />
      </div>
    </div>
  );
}

export function ThemeMiniature({ theme }: { theme: AppTheme }) {
  if (theme !== "system") return <Miniature theme={theme} />;
  return (
    <div className="relative h-full w-full">
      <Miniature theme="light" />
      <div className="absolute inset-0 [clip-path:polygon(100%_0,100%_100%,0_100%)]">
        <Miniature theme="dark" />
      </div>
    </div>
  );
}

/** The organization's theme - platform admin, on creating or editing a tenant. */
export default function ThemePicker({ value, onChange, userChoice, onUserChoice }: {
  value: AppTheme;
  onChange: (theme: AppTheme) => void;
  userChoice: boolean;
  onUserChoice: (allowed: boolean) => void;
}) {
  return (
    <div className="text-xs text-slate-400">
      Theme
      <div className="mt-1 grid grid-cols-2 gap-2 sm:grid-cols-4" role="radiogroup" aria-label="Theme">
        {THEMES.map((t) => {
          const on = value === t.value;
          return (
            <button key={t.value} type="button" role="radio" aria-checked={on} onClick={() => onChange(t.value)} title={t.hint}
              className={`overflow-hidden rounded-lg border text-left transition ${on ? "border-sky-500 ring-2 ring-sky-500/40" : "border-slate-700 hover:border-slate-500"}`}>
              <div className="h-16 w-full"><ThemeMiniature theme={t.value} /></div>
              <div className="border-t border-slate-800 px-2 py-1.5">
                <span className={`block text-xs font-semibold ${on ? "text-sky-300" : "text-slate-200"}`}>{t.label}</span>
                <span className="block text-[10px] leading-tight text-slate-500">{t.hint.split(" - ")[0]}</span>
              </div>
            </button>
          );
        })}
      </div>
      <label className="mt-2 flex items-center gap-2 text-xs text-slate-300">
        <input type="checkbox" checked={userChoice} onChange={(e) => onUserChoice(e.target.checked)} className="h-4 w-4 accent-sky-600" />
        Let each user switch to another theme for themselves
        <span className="text-slate-500">(from their avatar menu; this theme stays the default)</span>
      </label>
    </div>
  );
}

/** A user's own theme, when the organization allows it - in the top bar. */
export function UserThemeButton() {
  const state = useSyncExternalStore(subscribeTheme, () => `${currentResolvedTheme()}|${userMayChoose()}`);
  const [resolved, allowed] = state.split("|");
  const [open, setOpen] = useState(false);
  const [mine, setMine] = useState<AppTheme | null>(userTheme());
  if (allowed !== "true") return null;
  const choose = (t: AppTheme | null) => {
    setUserTheme(t);
    setMine(t);
    setOpen(false);
  };
  const org = THEMES.find((t) => t.value === organizationTheme());
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} title="Theme"
        className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-800 hover:text-slate-100">
        {resolved === "light" ? (
          <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
        ) : (
          <svg viewBox="0 0 24 24" className="h-[18px] w-[18px]" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" /></svg>
        )}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div role="menu" className="absolute right-0 top-11 z-50 w-64 rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-2xl">
            <p className="px-2 pb-1.5 pt-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Your theme</p>
            <button role="menuitemradio" aria-checked={mine === null} onClick={() => choose(null)}
              className={`flex w-full items-center justify-between rounded-lg px-2 py-1.5 text-left text-sm ${mine === null ? "bg-sky-500/10 text-sky-300" : "text-slate-200 hover:bg-slate-800"}`}>
              Organization default <span className="text-xs text-slate-500">{org?.label}</span>
            </button>
            {THEMES.map((t) => (
              <button key={t.value} role="menuitemradio" aria-checked={mine === t.value} onClick={() => choose(t.value)} title={t.hint}
                className={`mt-0.5 flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm ${mine === t.value ? "bg-sky-500/10 text-sky-300" : "text-slate-200 hover:bg-slate-800"}`}>
                <span className="h-6 w-9 shrink-0 overflow-hidden rounded border border-slate-700"><ThemeMiniature theme={t.value} /></span>
                {t.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
