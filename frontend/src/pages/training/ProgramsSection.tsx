import { useEffect, useState } from "react";
import { tr } from "./api";
import type { Catalog, Module, Objective, Program, Version } from "./api";
import { readable, useAction } from "./hooks";
import { Badge, Card, Note, btnDanger, btnGhost, btnPrimary, field, label } from "./ui";

/* Programs, curriculum modules and versions (PRG-001..006): draft -> published -> retired. */

function ModulesEditor({ modules, onChange, readOnly, objectives }: { modules: Module[]; onChange: (m: Module[]) => void; readOnly: boolean; objectives: Objective[] }) {
  const [drag, setDrag] = useState<number | null>(null);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= modules.length || from === to) return;
    const next = [...modules];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    onChange(next);
  };
  const set = (i: number, patch: Partial<Module>) => onChange(modules.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  return (
    <div className="space-y-2">
      {modules.map((m, i) => (
        <div
          key={i}
          draggable={!readOnly}
          onDragStart={() => setDrag(i)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => {
            if (drag !== null) move(drag, i);
            setDrag(null);
          }}
          className={`space-y-2 rounded-xl border bg-slate-950/40 p-3 ${drag === i ? "border-sky-500" : "border-slate-800"}`}
        >
          <div className="flex items-center gap-2">
            {!readOnly && (
              <span className="cursor-grab select-none text-slate-500" title="Drag to reorder" aria-hidden>
                ⠿
              </span>
            )}
            <span className="w-6 text-xs text-slate-500">{i + 1}.</span>
            <input className={field} disabled={readOnly} aria-label={`Module ${i + 1} title`} placeholder="Module title" value={m.title} onChange={(e) => set(i, { title: e.target.value })} />
            <input
              type="number"
              min={0}
              disabled={readOnly}
              aria-label="Duration in minutes"
              className="w-24 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 text-sm"
              value={m.duration_min}
              onChange={(e) => set(i, { duration_min: Number(e.target.value) })}
            />
            <span className="text-xs text-slate-500">min</span>
            {!readOnly && (
              <>
                <button type="button" aria-label="Move up" onClick={() => move(i, i - 1)}>
                  ↑
                </button>
                <button type="button" aria-label="Move down" onClick={() => move(i, i + 1)}>
                  ↓
                </button>
                <button type="button" className="text-xs text-red-300" onClick={() => onChange(modules.filter((_, j) => j !== i))}>
                  ✕
                </button>
              </>
            )}
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <label className={label}>
              Objectives covered
              <select
                multiple
                disabled={readOnly}
                className={`${field} h-16`}
                value={m.objectives}
                onChange={(e) => set(i, { objectives: Array.from(e.target.selectedOptions).map((o) => o.value) })}
              >
                {objectives.map((o) => (
                  <option key={o.code} value={o.code}>
                    {o.code} - {o.text}
                  </option>
                ))}
              </select>
            </label>
            <label className={label}>
              Materials (one link per line)
              <textarea
                disabled={readOnly}
                className={`${field} h-16`}
                value={m.materials.map((x) => x.url).join("\n")}
                onChange={(e) =>
                  set(i, {
                    materials: e.target.value
                      .split("\n")
                      .map((u) => u.trim())
                      .filter(Boolean)
                      .map((url) => ({ label: url, url })),
                  })
                }
              />
            </label>
          </div>
          <label className={label}>
            Facilitator notes
            <textarea disabled={readOnly} className={`${field} h-14`} value={m.notes} onChange={(e) => set(i, { notes: e.target.value })} />
          </label>
        </div>
      ))}
      {!readOnly && (
        <button type="button" className={btnGhost} onClick={() => onChange([...modules, { title: "", objectives: [], duration_min: 30, materials: [], notes: "" }])}>
          + Module
        </button>
      )}
    </div>
  );
}

function VersionEditor({ version, catalog, canEdit, onChanged }: { version: Version; catalog: Catalog; canEdit: boolean; onChanged: () => void }) {
  const [v, setV] = useState<Version>(version);
  const [problems, setProblems] = useState<string[]>([]);
  const action = useAction();
  const draft = v.state === "draft" && canEdit;
  const modules: Module[] = v.modules ?? v.curriculum.map((title) => ({ title, objectives: [], duration_min: 0, materials: [], notes: "" }));
  const objectives = v.objectives ?? [];
  const templates = catalog.templates ?? [];
  useEffect(() => {
    if (v.state === "draft") tr.checkVersion(v.id).then((r) => setProblems(r.problems)).catch(() => setProblems([]));
  }, [v]);
  const save = () =>
    action.run(async () => {
      const out = await tr.editVersion(v.id, {
        modules,
        objectives,
        duration_hours: v.duration_hours,
        pre_template_id: v.pre_template_id,
        post_template_id: v.post_template_id,
        feedback_template_id: v.feedback_template_id,
        assessments_waived: !!v.assessments_waived,
        waive_reason: v.waive_reason ?? "",
        required_certification_id: v.required_certification_id,
        version_label: v.version_label,
      });
      setV(out);
      onChanged();
      return out;
    }, "Draft saved.");
  const pick = (kind: "pre" | "post" | "feedback") => (
    <label className={label}>
      {kind === "pre" ? "Pre-assessment" : kind === "post" ? "Post-assessment" : "Feedback (optional)"}
      <select
        className={field}
        disabled={!draft}
        value={v[`${kind}_template_id`] ?? ""}
        onChange={(e) => setV({ ...v, [`${kind}_template_id`]: e.target.value ? Number(e.target.value) : null })}
      >
        <option value="">-</option>
        {templates
          .filter((t) => t.type === kind)
          .map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} v{t.version_label} ({t.state})
            </option>
          ))}
      </select>
    </label>
  );
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-bold">Version {v.version_label}</h4>
        <Badge value={v.state} />
        <span className="text-xs text-slate-500">{v.sessions} session(s)</span>
        {!draft && v.state === "published" && <span className="text-xs text-slate-500">Published versions are immutable - start a new version to change it.</span>}
      </div>
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Learning objectives</p>
        {objectives.map((o, i) => (
          <div key={i} className="flex gap-2">
            <input className="w-20 rounded-lg border border-slate-700 bg-slate-950 px-2 py-2 font-mono text-sm" disabled={!draft} value={o.code} aria-label="Objective code"
              onChange={(e) => setV({ ...v, objectives: objectives.map((x, j) => (j === i ? { ...x, code: e.target.value } : x)) })} />
            <input className={field} disabled={!draft} value={o.text} aria-label="Objective"
              onChange={(e) => setV({ ...v, objectives: objectives.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)) })} />
            {draft && (
              <button type="button" className="text-xs text-red-300" onClick={() => setV({ ...v, objectives: objectives.filter((_, j) => j !== i) })}>
                ✕
              </button>
            )}
          </div>
        ))}
        {draft && (
          <button type="button" className={btnGhost} onClick={() => setV({ ...v, objectives: [...objectives, { code: `LO${objectives.length + 1}`, text: "" }] })}>
            + Objective
          </button>
        )}
      </div>
      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Curriculum modules (drag to reorder)</p>
        <ModulesEditor modules={modules} objectives={objectives} readOnly={!draft} onChange={(m) => setV({ ...v, modules: m })} />
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        {pick("pre")}
        {pick("post")}
        {pick("feedback")}
      </div>
      <div className="grid gap-2 md:grid-cols-[auto_1fr_220px]">
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" disabled={!draft} checked={!!v.assessments_waived} onChange={(e) => setV({ ...v, assessments_waived: e.target.checked })} />
          Assessments not required
        </label>
        {v.assessments_waived ? (
          <input className={field} disabled={!draft} placeholder="Reason (required)" value={v.waive_reason ?? ""} onChange={(e) => setV({ ...v, waive_reason: e.target.value })} />
        ) : (
          <span />
        )}
        <label className={label}>
          Required facilitator certification
          <select className={field} disabled={!draft} value={v.required_certification_id ?? ""} onChange={(e) => setV({ ...v, required_certification_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">None</option>
            {(catalog.certifications ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {v.state === "draft" && problems.length > 0 && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          <p className="mb-1 font-semibold">Before this version can be published:</p>
          <ul className="list-disc space-y-0.5 pl-5">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      <Note>{action.error}</Note>
      <Note kind="ok">{action.ok}</Note>
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          {draft && (
            <>
              <button type="button" className={btnPrimary} disabled={action.busy} onClick={save}>
                Save draft
              </button>
              <button
                type="button"
                className={btnGhost}
                disabled={action.busy}
                onClick={async () => {
                  if (!(await save())) return;
                  const out = await action.run(() => tr.publishVersion(v.id), "Published.");
                  if (out) {
                    setV(out);
                    onChanged();
                  }
                }}
              >
                Publish
              </button>
            </>
          )}
          {v.state === "published" && (
            <button
              type="button"
              className={btnDanger}
              disabled={action.busy}
              onClick={async () => {
                const out = await action.run(() => tr.retireVersion(v.id), "Retired - it can no longer be used for new sessions.");
                if (out) {
                  setV(out);
                  onChanged();
                }
              }}
            >
              Retire
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ProgramForm({ certifications, onCreated }: { certifications: Catalog["certifications"]; onCreated: () => void }) {
  const [p, setP] = useState({ code: "", title: "", description: "", category: "", audience: "", delivery_mode: "in_person", duration_hours: "", objectives: "", required_certification_id: "" });
  const action = useAction();
  return (
    <Card title="New program">
      <form
        className="space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          const out = await action.run(
            () =>
              tr.addProgram({
                ...p,
                duration_hours: p.duration_hours ? Number(p.duration_hours) : null,
                objectives: p.objectives.split("\n").map((x) => x.trim()).filter(Boolean),
                required_certification_id: p.required_certification_id ? Number(p.required_certification_id) : null,
              }),
            "Program created with a draft version 1.0.",
          );
          if (out) onCreated();
        }}
      >
        <div className="grid gap-2 md:grid-cols-[140px_1fr]">
          <label className={label}>
            Code
            <input required className={field} value={p.code} onChange={(e) => setP({ ...p, code: e.target.value })} />
          </label>
          <label className={label}>
            Title
            <input required className={field} value={p.title} onChange={(e) => setP({ ...p, title: e.target.value })} />
          </label>
        </div>
        <label className={label}>
          Description
          <textarea className={field} value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} />
        </label>
        <div className="grid gap-2 md:grid-cols-4">
          <label className={label}>
            Category
            <input className={field} value={p.category} onChange={(e) => setP({ ...p, category: e.target.value })} />
          </label>
          <label className={label}>
            Target audience
            <input className={field} value={p.audience} onChange={(e) => setP({ ...p, audience: e.target.value })} />
          </label>
          <label className={label}>
            Delivery
            <select className={field} value={p.delivery_mode} onChange={(e) => setP({ ...p, delivery_mode: e.target.value })}>
              <option value="in_person">In person</option>
              <option value="virtual">Virtual</option>
              <option value="hybrid">Hybrid</option>
            </select>
          </label>
          <label className={label}>
            Duration (hours)
            <input type="number" min={0} step={0.5} className={field} value={p.duration_hours} onChange={(e) => setP({ ...p, duration_hours: e.target.value })} />
          </label>
        </div>
        <label className={label}>
          Learning objectives (one per line)
          <textarea className={field} value={p.objectives} onChange={(e) => setP({ ...p, objectives: e.target.value })} />
        </label>
        <label className={label}>
          Required facilitator certification
          <select className={field} value={p.required_certification_id} onChange={(e) => setP({ ...p, required_certification_id: e.target.value })}>
            <option value="">None</option>
            {(certifications ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <Note>{action.error}</Note>
        <Note kind="ok">{action.ok}</Note>
        <button className={btnPrimary} disabled={action.busy}>
          Create program
        </button>
      </form>
    </Card>
  );
}

function Diff({ program }: { program: Program }) {
  const [a, setA] = useState<number | "">("");
  const [b, setB] = useState<number | "">("");
  const [out, setOut] = useState<Awaited<ReturnType<typeof tr.diff>> | null>(null);
  const [error, setError] = useState("");
  if (program.versions.length < 2) return null;
  return (
    <div className="space-y-2 rounded-xl border border-slate-800 p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Compare versions</p>
      <div className="flex flex-wrap gap-2">
        {[
          [a, setA],
          [b, setB],
        ].map(([value, set], i) => (
          <select key={i} className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm" aria-label={i ? "To version" : "From version"} value={value as number | ""}
            onChange={(e) => (set as (v: number | "") => void)(e.target.value ? Number(e.target.value) : "")}>
            <option value="">{i ? "to…" : "from…"}</option>
            {program.versions.map((v) => (
              <option key={v.id} value={v.id}>
                v{v.version_label}
              </option>
            ))}
          </select>
        ))}
        <button type="button" className={btnGhost} disabled={!a || !b} onClick={() => tr.diff(Number(a), Number(b)).then(setOut).catch((e) => setError(readable(e)))}>
          Compare
        </button>
      </div>
      <Note>{error}</Note>
      {out && (
        <ul className="space-y-1 text-sm">
          {out.added.map((t) => (
            <li key={`a${t}`} className="text-emerald-300">+ {t}</li>
          ))}
          {out.removed.map((t) => (
            <li key={`r${t}`} className="text-red-300">− {t}</li>
          ))}
          {out.changed.map((c) => (
            <li key={`c${c.title}`} className="text-amber-200">
              ~ {c.title}: {c.fields.join(", ")}
            </li>
          ))}
          {!out.added.length && !out.removed.length && !out.changed.length && <li className="text-slate-400">No module changes.</li>}
        </ul>
      )}
    </div>
  );
}

export default function ProgramsSection({ catalog, canEdit, onChanged }: { catalog: Catalog; canEdit: boolean; onChanged: () => void }) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [versionId, setVersionId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  const action = useAction();
  const program = catalog.programs.find((p) => p.id === openId) ?? null;
  const version = program?.versions.find((v) => v.id === versionId) ?? program?.versions[program.versions.length - 1] ?? null;
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <Card
        title="Programs"
        actions={
          canEdit && (
            <button type="button" className={btnPrimary} onClick={() => setCreating(true)}>
              + Program
            </button>
          )
        }
      >
        <ul className="space-y-1">
          {catalog.programs.map((p) => {
            const latest = p.versions[p.versions.length - 1];
            return (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => {
                    setOpenId(p.id);
                    setVersionId(null);
                    setCreating(false);
                  }}
                  className={`flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm ${openId === p.id ? "bg-sky-500/10 text-sky-200" : "text-slate-200 hover:bg-slate-800/60"}`}
                >
                  <span className="font-mono text-[11px] text-slate-500">{p.code ?? "-"}</span>
                  <span className="min-w-0 flex-1 truncate">{p.name}</span>
                  {latest && <Badge value={latest.state}>v{latest.version_label}</Badge>}
                </button>
              </li>
            );
          })}
          {!catalog.programs.length && <li className="text-sm text-slate-500">No programs yet.</li>}
        </ul>
      </Card>
      <div className="space-y-4">
        {creating && (
          <ProgramForm
            certifications={catalog.certifications}
            onCreated={() => {
              setCreating(false);
              onChanged();
            }}
          />
        )}
        {program && !creating && (
          <Card
            title={
              <span>
                {program.name} <span className="font-mono text-xs text-slate-500">{program.code}</span>
              </span>
            }
            actions={
              canEdit && (
                <>
                  {(["minor", "major"] as const).map((bump) => (
                    <button
                      key={bump}
                      type="button"
                      className={btnGhost}
                      disabled={action.busy || program.versions.some((v) => v.state === "draft")}
                      title={program.versions.some((v) => v.state === "draft") ? "Finish the current draft first" : undefined}
                      onClick={async () => {
                        const out = await action.run(() => tr.newVersion(program.id, version?.id ?? null, bump), "New draft version created.");
                        if (out) {
                          setVersionId(out.id);
                          onChanged();
                        }
                      }}
                    >
                      New {bump} version
                    </button>
                  ))}
                </>
              )
            }
          >
            <div className="space-y-4">
              <p className="text-sm text-slate-400">
                {program.description || "No description."} · {program.delivery_mode?.replace("_", " ") ?? "in person"}
                {program.duration_hours ? ` · ${program.duration_hours} h` : ""}
                {program.audience ? ` · for ${program.audience}` : ""}
              </p>
              <div className="flex flex-wrap gap-1">
                {program.versions.map((v) => (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => setVersionId(v.id)}
                    className={`rounded-full border px-2.5 py-1 text-xs ${version?.id === v.id ? "border-sky-500 text-sky-200" : "border-slate-700 text-slate-300"}`}
                  >
                    v{v.version_label} · {v.state}
                  </button>
                ))}
              </div>
              <Note>{action.error}</Note>
              {version && <VersionEditor key={version.id} version={version} catalog={catalog} canEdit={canEdit} onChanged={onChanged} />}
              <Diff program={program} />
            </div>
          </Card>
        )}
        {!program && !creating && <Note kind="info">Choose a program to see its versions, curriculum and assessments.</Note>}
      </div>
    </div>
  );
}
