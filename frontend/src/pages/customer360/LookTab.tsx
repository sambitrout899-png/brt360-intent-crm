import type { C360Layout, C360Studio, C360ThemeSettings } from "../../api/c360Types";

const SWATCHES = ["#e60000", "#38bdf8", "#22d3ee", "#14b8a6", "#22c55e", "#a855f7", "#ec4899", "#f97316", "#f59e0b", "#6366f1"];

function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex overflow-hidden rounded-lg border border-slate-700 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={`flex-1 py-1.5 font-semibold ${value === o.value ? "bg-sky-600 text-white" : "text-slate-400 hover:bg-slate-800"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Look & feel: theme, accent, card style, spacing - and whole-page templates. */
export default function LookTab({ studio, draft, setDraft }: { studio: C360Studio; draft: C360Layout; setDraft: (next: C360Layout) => void }) {
  const theme = draft.theme;
  const set = (patch: Partial<C360ThemeSettings>) => setDraft({ ...draft, theme: { ...theme, ...patch } });

  return (
    <div className="space-y-5">
      <section>
        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Theme</h3>
        <div className="grid grid-cols-2 gap-2">
          {studio.catalog.themes.map((t) => (
            <button
              key={t.key}
              onClick={() => set({ preset: t.key })}
              className={`overflow-hidden rounded-xl border text-left transition ${theme.preset === t.key ? "border-sky-400 ring-2 ring-sky-500/40" : "border-slate-700 hover:border-slate-500"}`}
            >
              <div className="h-14 p-2" style={{ background: t.hero }}>
                <div className="flex gap-1">
                  <span className="h-6 w-10 rounded" style={{ background: t.surface, border: `1px solid ${t.border}` }} />
                  <span className="h-6 w-6 rounded" style={{ background: theme.accent ?? t.accent }} />
                </div>
              </div>
              <div className="px-2 py-1.5 text-xs font-semibold" style={{ background: t.bg, color: t.text }}>
                {t.label}
              </div>
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Accent colour</h3>
        <div className="flex flex-wrap items-center gap-1.5">
          {SWATCHES.map((c) => (
            <button
              key={c}
              onClick={() => set({ accent: c })}
              className={`h-7 w-7 rounded-full border-2 transition ${theme.accent === c ? "scale-110 border-white" : "border-transparent"}`}
              style={{ background: c }}
              title={c}
            />
          ))}
          <input type="color" value={theme.accent ?? "#38bdf8"} onChange={(e) => set({ accent: e.target.value })} className="h-7 w-9 cursor-pointer rounded border border-slate-700 bg-slate-900" title="Any colour" />
        </div>
        <div className="mt-2 flex gap-2 text-[11px]">
          {studio.brand_color && (
            <button onClick={() => set({ accent: studio.brand_color })} className="rounded-md border border-slate-700 px-2 py-1 font-semibold text-slate-300 hover:border-slate-500">
              <span className="mr-1 inline-block h-2.5 w-2.5 rounded-full align-middle" style={{ background: studio.brand_color }} />
              Our brand colour
            </button>
          )}
          <button onClick={() => set({ accent: null })} className="rounded-md border border-slate-700 px-2 py-1 font-semibold text-slate-400 hover:border-slate-500">
            Theme default
          </button>
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">Cards</h3>
          <Segmented value={theme.card_style} onChange={(v) => set({ card_style: v })} options={[{ value: "glass", label: "Glass" }, { value: "solid", label: "Solid" }, { value: "outline", label: "Outline" }]} />
        </div>
        <div>
          <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">Spacing</h3>
          <Segmented value={theme.density} onChange={(v) => set({ density: v })} options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} />
        </div>
        <div>
          <h3 className="mb-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">Header</h3>
          <Segmented value={theme.header_style} onChange={(v) => set({ header_style: v })} options={[{ value: "hero", label: "Hero" }, { value: "compact", label: "Compact" }]} />
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Start from a template</h3>
        <div className="space-y-1.5">
          {studio.catalog.templates.map((t) => (
            <button
              key={t.key}
              onClick={() => {
                const tpl = studio.template_layouts[t.key];
                if (tpl) setDraft({ ...tpl, theme: draft.theme, header: draft.header });
              }}
              className={`w-full rounded-lg border px-3 py-2 text-left transition hover:border-sky-500 ${draft.template === t.key ? "border-sky-500/60 bg-sky-500/5" : "border-slate-700"}`}
            >
              <span className="block text-xs font-bold text-slate-100">{t.label}</span>
              <span className="block text-[11px] text-slate-500">{t.description}</span>
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-slate-500">A template replaces the panes - colours and headline numbers stay.</p>
      </section>
    </div>
  );
}
