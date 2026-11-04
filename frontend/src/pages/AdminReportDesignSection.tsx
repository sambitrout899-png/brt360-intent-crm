import { useEffect, useRef, useState } from "react";
import { getReportDesign, previewReportDesign, resetReportDesign, saveReportDesign, type ReportDesign, type ReportDesignSettings } from "../api/reportDesign";

/** Admin -> Report Design: the organization's look for every PDF the platform produces - test evidence, bulk
 * order results, analytics and audit exports, training reports, proposals and agreements. Logo, colour, header,
 * company line, footer, confidentiality line, watermark and page size, with a live preview of a sample report. */

const MAX_LOGO = 512 * 1024;
const SWATCHES = ["#0d5c8c", "#1d4ed8", "#0f766e", "#15803d", "#b91c1c", "#c2410c", "#7c3aed", "#be185d", "#334155", "#111827"];
const HEADER_LOOK: Record<string, { band: string; text: string; edge: string }> = {
  band: { band: "var(--accent)", text: "#fff", edge: "var(--accent)" },
  tint: { band: "color-mix(in srgb, var(--accent) 14%, white)", text: "var(--accent)", edge: "var(--accent)" },
  clean: { band: "#fff", text: "var(--accent)", edge: "var(--accent)" },
};

function detail(e: unknown): string {
  const text = e instanceof Error ? e.message : String(e);
  const json = text.slice(text.indexOf("{"));
  try {
    const parsed = JSON.parse(json);
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* not JSON */
  }
  return text;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

const input = "w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none";

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
      <h3 className="mb-3 text-sm font-semibold text-slate-200">{title}</h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

export default function AdminReportDesignSection() {
  const [design, setDesign] = useState<ReportDesign | null>(null);
  const [form, setForm] = useState<ReportDesignSettings | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const urlRef = useRef("");

  function apply(d: ReportDesign) {
    setDesign(d);
    setForm(d.settings);
  }

  useEffect(() => {
    getReportDesign().then(apply).catch((e) => setError(detail(e)));
    return () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, []);

  // the live preview follows the form (a moment after the last change)
  useEffect(() => {
    if (!form) return;
    const timer = window.setTimeout(() => {
      previewReportDesign(form)
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          if (urlRef.current) URL.revokeObjectURL(urlRef.current);
          urlRef.current = url;
          setPreviewUrl(url);
          setPreviewError("");
        })
        .catch((e) => setPreviewError(detail(e)));
    }, 600);
    return () => window.clearTimeout(timer);
  }, [form]);

  if (!form || !design) return <p className="text-sm text-slate-400">{error || "Loading..."}</p>;
  const set = (patch: Partial<ReportDesignSettings>) => setForm({ ...form, ...patch });
  const dirty = JSON.stringify(form) !== JSON.stringify(design.settings);
  const accent = /^#[0-9a-fA-F]{6}$/.test(form.accent) ? form.accent : design.app_brand_color || "#0d5c8c";

  const flash = (text: string) => {
    setMessage(text);
    window.setTimeout(() => setMessage(""), 4000);
  };

  function upload(file: File | undefined) {
    if (!file) return;
    if (!["image/png", "image/jpeg"].includes(file.type)) {
      setError("The report logo must be a PNG or JPEG (PDF reports can't show SVG, GIF or WebP).");
      return;
    }
    if (file.size > MAX_LOGO) {
      setError(`The logo is ${Math.round(file.size / 1024)} KB - the limit is 512 KB.`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setError("");
      set({ logo: "custom", logo_data_url: String(reader.result) });
    };
    reader.readAsDataURL(file);
  }

  async function save() {
    setBusy("save");
    setError("");
    try {
      apply(await saveReportDesign(form!));
      flash("Saved - every PDF report uses this design from now on.");
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy("");
    }
  }

  async function reset() {
    if (!window.confirm("Go back to the standard report design (the app's logo and brand colour)?")) return;
    setBusy("reset");
    setError("");
    try {
      apply(await resetReportDesign());
      flash("Back to the standard design.");
    } catch (e) {
      setError(detail(e));
    } finally {
      setBusy("");
    }
  }

  const appLogoUsable = !!design.app_logo_data_url && /^data:image\/(png|jpeg)/.test(design.app_logo_data_url);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">📑 Report Design</h2>
          <p className="max-w-3xl text-sm text-slate-400">
            How your organization's PDF reports look: test evidence, bulk order results, analytics and audit exports, training reports,
            proposals and agreements all use this design. The preview on the right updates as you change it, and nothing changes until you save.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {message && <span className="text-sm text-emerald-400">{message}</span>}
          {design.customized && (
            <button onClick={reset} disabled={!!busy} className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 disabled:opacity-50">
              Reset to standard
            </button>
          )}
          {previewUrl && (
            <a href={previewUrl} download="report-design-sample.pdf" className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500">
              Download sample
            </a>
          )}
          <button onClick={save} disabled={!!busy || !dirty} className="rounded-md bg-sky-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50">
            {busy === "save" ? "Saving..." : dirty ? "Save design" : "Saved"}
          </button>
        </div>
      </header>
      {error && <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <div className="space-y-4" style={{ ["--accent" as string]: accent }}>
          <Card title="Logo">
            <div className="grid gap-2">
              {([
                ["app", "The app's logo", appLogoUsable ? "The logo in the app header" : design.app_logo_data_url ? "The app logo isn't a PNG or JPEG - PDFs can't show it" : "No app logo yet - the platform administrator sets it"],
                ["custom", "A logo just for reports", "PNG or JPEG, up to 512 KB - a wide logo on a transparent background works best"],
                ["none", "No logo", "The company name only"],
              ] as const).map(([key, title, text]) => (
                <label key={key} className={`flex cursor-pointer gap-3 rounded-lg border p-2.5 ${form.logo === key ? "border-sky-500 bg-sky-500/10" : "border-slate-800 hover:border-slate-600"}`}>
                  <input type="radio" name="logo" className="mt-1" checked={form.logo === key} onChange={() => set({ logo: key })} />
                  <span className="flex-1">
                    <span className="block text-sm font-semibold text-slate-200">{title}</span>
                    <span className="block text-[11px] text-slate-500">{text}</span>
                  </span>
                  {key === "app" && design.app_logo_data_url && <img src={design.app_logo_data_url} alt="App logo" className="h-8 max-w-[90px] rounded bg-white object-contain p-0.5" />}
                  {key === "custom" && form.logo_data_url && <img src={form.logo_data_url} alt="Report logo" className="h-8 max-w-[90px] rounded bg-white object-contain p-0.5" />}
                </label>
              ))}
            </div>
            {form.logo === "custom" && (
              <div className="flex items-center gap-2">
                <label className="cursor-pointer rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500">
                  {form.logo_data_url ? "Replace..." : "Upload..."}
                  <input type="file" accept="image/png,image/jpeg" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
                </label>
                {form.logo_data_url && (
                  <button onClick={() => set({ logo_data_url: null })} className="text-xs text-slate-500 hover:text-red-400">
                    Remove
                  </button>
                )}
              </div>
            )}
            {form.logo !== "none" && (
              <Field label="Position">
                <div className="flex gap-2">
                  {(["left", "right"] as const).map((p) => (
                    <button key={p} onClick={() => set({ logo_position: p })}
                      className={`rounded-md border px-3 py-1 text-sm capitalize ${form.logo_position === p ? "border-sky-500 bg-sky-500/10 text-sky-300" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
                      {p}
                    </button>
                  ))}
                </div>
              </Field>
            )}
            {!dirty && <p className="text-[11px] text-slate-500">On saved reports: {design.effective.logo_note}</p>}
          </Card>

          <Card title="Colour and header">
            <Field label="Accent colour" hint="Header, headings, table header rows, call-outs and rules. Blank = the app's brand colour.">
              <div className="flex flex-wrap items-center gap-2">
                <input type="color" value={accent} onChange={(e) => set({ accent: e.target.value })} className="h-8 w-11 cursor-pointer rounded border border-slate-700 bg-slate-950" />
                <input value={form.accent} placeholder={design.app_brand_color || "#0d5c8c"} onChange={(e) => set({ accent: e.target.value.trim() })} className={`${input} !w-28 font-mono`} />
                {form.accent && (
                  <button onClick={() => set({ accent: "" })} className="text-xs text-slate-500 hover:text-slate-300">
                    Use the app's colour
                  </button>
                )}
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {SWATCHES.map((c) => (
                  <button key={c} title={c} onClick={() => set({ accent: c })} className={`h-5 w-5 rounded-full border ${form.accent === c ? "border-white" : "border-slate-700"}`} style={{ background: c }} />
                ))}
              </div>
            </Field>
            <Field label="Header style">
              <div className="grid grid-cols-3 gap-2">
                {design.header_styles.map((s) => {
                  const look = HEADER_LOOK[s.key];
                  return (
                    <button key={s.key} onClick={() => set({ header_style: s.key })}
                      className={`overflow-hidden rounded-lg border text-left ${form.header_style === s.key ? "border-sky-500 ring-1 ring-sky-500" : "border-slate-700 hover:border-slate-500"}`}>
                      <div className="bg-white">
                        <div className="px-2 py-2" style={{ background: look.band, borderBottom: `3px solid ${look.edge}` }}>
                          <div className="h-1.5 w-12 rounded" style={{ background: look.text }} />
                          <div className="mt-1 h-1 w-8 rounded opacity-60" style={{ background: look.text }} />
                        </div>
                        <div className="space-y-1 px-2 py-2">
                          <div className="h-1 w-full rounded bg-slate-200" />
                          <div className="h-1 w-3/4 rounded bg-slate-200" />
                        </div>
                      </div>
                      <span className="block bg-slate-950 px-2 py-1 text-[11px] text-slate-300">{s.label}</span>
                    </button>
                  );
                })}
              </div>
            </Field>
          </Card>

          <Card title="Company line">
            <Field label="Company name" hint="Shown in the header and the footer.">
              <input value={form.company_name} placeholder={design.organization} maxLength={120} onChange={(e) => set({ company_name: e.target.value })} className={input} />
            </Field>
            <Field label="Tagline" hint="Under the name on the first page: an address, a website, a registration number ...">
              <input value={form.tagline} maxLength={160} placeholder="1 Harbour Road, Mumbai · www.example.com" onChange={(e) => set({ tagline: e.target.value })} className={input} />
            </Field>
          </Card>

          <Card title="Footer, watermark and page">
            <Field label="Footer text" hint={`Blank = "company · report title". You can use ${design.placeholders.join(" ")}.`}>
              <input value={form.footer_text} maxLength={200} placeholder="{company} · {title}" onChange={(e) => set({ footer_text: e.target.value })} className={input} />
            </Field>
            <Field label="Confidentiality line" hint="A second footer line on every page.">
              <input value={form.confidentiality} maxLength={200} placeholder="Confidential - for internal use only" onChange={(e) => set({ confidentiality: e.target.value })} className={input} />
            </Field>
            <Field label="Watermark" hint="Large see-through text across every page. Leave blank for none.">
              <div className="flex flex-wrap items-center gap-2">
                <input value={form.watermark} maxLength={24} placeholder="e.g. DRAFT" onChange={(e) => set({ watermark: e.target.value })} className={`${input} !w-40`} />
                {["DRAFT", "CONFIDENTIAL", "INTERNAL"].map((w) => (
                  <button key={w} onClick={() => set({ watermark: form.watermark === w ? "" : w })}
                    className={`rounded-full border px-2 py-0.5 text-[11px] ${form.watermark === w ? "border-sky-500 text-sky-300" : "border-slate-700 text-slate-400 hover:border-slate-500"}`}>
                    {w}
                  </button>
                ))}
              </div>
            </Field>
            <div className="flex flex-wrap items-center gap-4">
              <Field label="Page size">
                <select value={form.page_size} onChange={(e) => set({ page_size: e.target.value as ReportDesignSettings["page_size"] })} className={`${input} !w-28`}>
                  {design.page_sizes.map((p) => (
                    <option key={p}>{p}</option>
                  ))}
                </select>
              </Field>
              <label className="mt-4 flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={form.show_generated} onChange={(e) => set({ show_generated: e.target.checked })} />
                Show "Generated date and time" in the footer
              </label>
            </div>
          </Card>
        </div>

        <section className="flex min-h-[70vh] flex-col rounded-xl border border-slate-800 bg-slate-900/40 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-200">Preview: a sample report</h3>
            {dirty && <span className="text-[11px] text-amber-300">Not saved yet</span>}
          </div>
          {previewError && <p className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{previewError}</p>}
          {previewUrl ? (
            <iframe title="Report preview" src={previewUrl} className="min-h-[70vh] w-full flex-1 rounded-md border border-slate-800 bg-white" />
          ) : (
            <p className="text-sm text-slate-500">Building the preview...</p>
          )}
        </section>
      </div>
    </div>
  );
}
