import { useState } from "react";
import { tr } from "./api";
import type { Measure, Question, QuestionType, Template } from "./api";
import { useAction } from "./hooks";
import { Badge, Card, Note, btnGhost, btnPrimary, field, label } from "./ui";

/* Assessment template builder (ASM-001..007): pre / post / feedback, versioned;
   a published or in-use template is read-only and changes go into a clone. */

const TYPES: [QuestionType, string][] = [
  ["single", "Single choice"],
  ["multi", "Multiple choice"],
  ["true_false", "True / false"],
  ["likert5", "Likert 1-5"],
  ["rating10", "Rating 1-10"],
  ["nps", "NPS 0-10"],
  ["short_text", "Short text"],
  ["long_text", "Long text"],
];
const MEASURES: [Measure, string][] = [
  ["knowledge", "Knowledge (scored)"],
  ["confidence", "Confidence"],
  ["outcome", "Learning outcome"],
  ["feedback", "Feedback"],
];
const CHOICE = ["single", "multi", "true_false"];

function blank(n: number, kind: Template["type"]): Question {
  return kind === "feedback"
    ? { item_key: `f${n}`, type: "likert5", text: "", options: [], correct_options: [], points: 0, measure: "feedback", objective: null, required: true }
    : { item_key: `k${n}`, type: "single", text: "", options: ["", ""], correct_options: [0], points: 1, measure: "knowledge", objective: null, required: true };
}

function QuestionEditor({ q, onChange, onRemove, onMove, readOnly, objectives }: {
  q: Question;
  onChange: (q: Question) => void;
  onRemove: () => void;
  onMove: (d: -1 | 1) => void;
  readOnly: boolean;
  objectives: string[];
}) {
  const set = (patch: Partial<Question>) => onChange({ ...q, ...patch });
  const choice = CHOICE.includes(q.type);
  return (
    <fieldset disabled={readOnly} className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/40 p-3">
      <div className="grid gap-2 md:grid-cols-[140px_1fr]">
        <label className={label}>
          Item key (pairs pre ↔ post)
          <input className={`${field} font-mono`} value={q.item_key} onChange={(e) => set({ item_key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") })} />
        </label>
        <label className={label}>
          Question
          <input className={field} value={q.text} onChange={(e) => set({ text: e.target.value })} />
        </label>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <label className={label}>
          Type
          <select
            className={field}
            value={q.type}
            onChange={(e) => {
              const type = e.target.value as QuestionType;
              set({
                type,
                options: type === "true_false" ? ["True", "False"] : CHOICE.includes(type) ? (q.options.length >= 2 ? q.options : ["", ""]) : [],
                correct_options: CHOICE.includes(type) ? [0] : [],
                measure: type === "likert5" && q.measure === "knowledge" ? "confidence" : !CHOICE.includes(type) && q.measure === "knowledge" ? "feedback" : q.measure,
              });
            }}
          >
            {TYPES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className={label}>
          Measures
          <select className={field} value={q.measure} onChange={(e) => set({ measure: e.target.value as Measure, points: e.target.value === "knowledge" ? q.points || 1 : 0 })}>
            {MEASURES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className={label}>
          Learning objective
          <input className={field} list="objective-codes" placeholder="LO1" value={q.objective ?? ""} onChange={(e) => set({ objective: e.target.value || null })} />
          <datalist id="objective-codes">
            {objectives.map((o) => (
              <option key={o} value={o} />
            ))}
          </datalist>
        </label>
      </div>
      {choice && (
        <div className="space-y-1.5">
          <p className="text-xs text-slate-400">{q.measure === "knowledge" ? "Options - tick the correct answer(s)" : "Options"}</p>
          {q.options.map((o, i) => (
            <div key={i} className="flex items-center gap-2">
              {q.measure === "knowledge" && (
                <input
                  type={q.type === "multi" ? "checkbox" : "radio"}
                  aria-label={`Option ${i + 1} is correct`}
                  className="h-4 w-4 accent-emerald-500"
                  checked={q.correct_options.includes(i)}
                  onChange={(e) =>
                    set({ correct_options: q.type === "multi" ? (e.target.checked ? [...q.correct_options, i] : q.correct_options.filter((c) => c !== i)) : [i] })
                  }
                />
              )}
              <input
                className={field}
                aria-label={`Option ${i + 1}`}
                value={o}
                disabled={readOnly || q.type === "true_false"}
                onChange={(e) => set({ options: q.options.map((x, j) => (j === i ? e.target.value : x)) })}
              />
              {q.type !== "true_false" && q.options.length > 2 && (
                <button type="button" className="text-xs text-slate-500 hover:text-red-300" onClick={() => set({ options: q.options.filter((_, j) => j !== i), correct_options: q.correct_options.filter((c) => c !== i).map((c) => (c > i ? c - 1 : c)) })}>
                  ✕
                </button>
              )}
            </div>
          ))}
          {q.type !== "true_false" && q.options.length < 12 && (
            <button type="button" className="text-xs font-semibold text-sky-300" onClick={() => set({ options: [...q.options, ""] })}>
              + option
            </button>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        {q.measure === "knowledge" && (
          <label className="flex items-center gap-1">
            Points
            <input type="number" min={0.5} step={0.5} className="w-16 rounded border border-slate-700 bg-slate-950 px-2 py-1" value={q.points} onChange={(e) => set({ points: Number(e.target.value) })} />
          </label>
        )}
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={q.required} onChange={(e) => set({ required: e.target.checked })} /> Required
        </label>
        {!readOnly && (
          <span className="ml-auto flex gap-2">
            <button type="button" aria-label="Move up" onClick={() => onMove(-1)}>
              ↑
            </button>
            <button type="button" aria-label="Move down" onClick={() => onMove(1)}>
              ↓
            </button>
            <button type="button" className="text-red-300" onClick={onRemove}>
              Remove
            </button>
          </span>
        )}
      </div>
    </fieldset>
  );
}

export function TemplateEditor({ initial, onSaved, onClose }: { initial: Template | null; onSaved: (t: Template) => void; onClose: () => void }) {
  const [t, setT] = useState<Template>(
    initial ?? { id: 0, type: "pre", name: "", version_label: "1.0", family_id: null, state: "draft", settings: { one_attempt: true }, sections: [], questions: [blank(1, "pre")] },
  );
  const action = useAction();
  const readOnly = !!initial && (initial.state !== "draft" || !!initial.in_use);
  const objectives = Array.from(new Set(t.questions.map((q) => q.objective).filter(Boolean) as string[]));
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= t.questions.length) return;
    const qs = [...t.questions];
    [qs[i], qs[j]] = [qs[j], qs[i]];
    setT({ ...t, questions: qs });
  };
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          {initial ? `${initial.name} v${initial.version_label}` : "New assessment template"}
          {initial && <Badge value={initial.state} />}
          {readOnly && <span className="text-xs font-normal text-slate-400">read-only - clone it to change (ASM-001)</span>}
        </span>
      }
      actions={
        <button type="button" className={btnGhost} onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-2 sm:grid-cols-[1fr_140px_100px]">
          <label className={label}>
            Name
            <input className={field} disabled={readOnly} value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} />
          </label>
          <label className={label}>
            Type
            <select className={field} disabled={!!initial} value={t.type} onChange={(e) => setT({ ...t, type: e.target.value as Template["type"] })}>
              <option value="pre">Pre-assessment</option>
              <option value="post">Post-assessment</option>
              <option value="feedback">Feedback</option>
            </select>
          </label>
          <label className={label}>
            Version
            <input className={field} disabled={readOnly} value={t.version_label} onChange={(e) => setT({ ...t, version_label: e.target.value })} />
          </label>
        </div>
        <div className="flex flex-wrap gap-4 text-xs text-slate-300">
          {(
            [
              ["randomize_questions", "Randomize question order"],
              ["randomize_options", "Randomize option order"],
              ["one_attempt", "One attempt only"],
            ] as const
          ).map(([k, l]) => (
            <label key={k} className="flex items-center gap-1.5">
              <input type="checkbox" disabled={readOnly} checked={!!t.settings[k]} onChange={(e) => setT({ ...t, settings: { ...t.settings, [k]: e.target.checked } })} />
              {l}
            </label>
          ))}
          <label className="flex items-center gap-1.5">
            Time limit (min)
            <input
              type="number"
              min={0}
              disabled={readOnly}
              className="w-16 rounded border border-slate-700 bg-slate-950 px-2 py-0.5"
              value={t.settings.time_limit_min ?? ""}
              onChange={(e) => setT({ ...t, settings: { ...t.settings, time_limit_min: e.target.value ? Number(e.target.value) : null } })}
            />
          </label>
        </div>
        <p className="text-xs text-slate-500">
          Pre and post templates of one program version must share at least one Knowledge and one Confidence item with the same item key, and every Knowledge / Confidence
          question needs a learning objective code (ASM-004, ASM-005).
        </p>
        {t.questions.map((q, i) => (
          <QuestionEditor
            key={i}
            q={q}
            readOnly={readOnly}
            objectives={objectives}
            onChange={(nq) => setT({ ...t, questions: t.questions.map((x, j) => (j === i ? nq : x)) })}
            onRemove={() => setT({ ...t, questions: t.questions.filter((_, j) => j !== i) })}
            onMove={(d) => move(i, d)}
          />
        ))}
        <Note>{action.error}</Note>
        <Note kind="ok">{action.ok}</Note>
        <div className="flex flex-wrap gap-2">
          {!readOnly && (
            <>
              <button type="button" className={btnGhost} onClick={() => setT({ ...t, questions: [...t.questions, blank(t.questions.length + 1, t.type)] })}>
                + Question
              </button>
              <button
                type="button"
                className={btnPrimary}
                disabled={action.busy}
                onClick={async () => {
                  const saved = await action.run(() => tr.saveTemplate(t, initial?.id), "Saved.");
                  if (saved) {
                    setT({ ...saved, in_use: false });
                    onSaved(saved);
                  }
                }}
              >
                Save draft
              </button>
            </>
          )}
          {initial && initial.state === "draft" && !readOnly && (
            <button type="button" className={btnGhost} disabled={action.busy} onClick={async () => {
              const out = await action.run(() => tr.publishTemplate(initial.id), "Published - it is now read-only.");
              if (out) onSaved(out);
            }}>
              Publish
            </button>
          )}
          {initial && (
            <button type="button" className={btnGhost} disabled={action.busy} onClick={async () => {
              const out = await action.run(() => tr.cloneTemplate(initial.id), "Cloned as a new draft.");
              if (out) onSaved(out);
            }}>
              Clone to new version
            </button>
          )}
        </div>
      </div>
    </Card>
  );
}

export default function TemplatesSection({ templates, canEdit, onChanged }: { templates: Template[]; canEdit: boolean; onChanged: () => void }) {
  const [open, setOpen] = useState<Template | null | "new">(null);
  const [filter, setFilter] = useState<"" | Template["type"]>("");
  return (
    <div className="space-y-4">
      {open && (
        <TemplateEditor
          key={open === "new" ? "new" : open.id}
          initial={open === "new" ? null : open}
          onClose={() => setOpen(null)}
          onSaved={(t) => {
            onChanged();
            setOpen({ ...t, in_use: t.state !== "draft" });
          }}
        />
      )}
      <Card
        title="Assessment templates"
        actions={
          <>
            <select className="rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm" value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} aria-label="Filter by type">
              <option value="">All types</option>
              <option value="pre">Pre</option>
              <option value="post">Post</option>
              <option value="feedback">Feedback</option>
            </select>
            {canEdit && (
              <button type="button" className={btnPrimary} onClick={() => setOpen("new")}>
                + Template
              </button>
            )}
          </>
        }
      >
        <ul className="divide-y divide-slate-800">
          {templates
            .filter((t) => !filter || t.type === filter)
            .map((t) => (
              <li key={t.id} className="flex flex-wrap items-center gap-2 py-2">
                <span className="w-16 text-[11px] font-semibold uppercase text-slate-400">{t.type}</span>
                <span className="min-w-0 flex-1 truncate text-sm text-slate-100">
                  {t.name} <span className="text-slate-500">v{t.version_label}</span>
                </span>
                <span className="text-xs text-slate-500">{t.questions.length} questions</span>
                <Badge value={t.state} />
                {t.in_use && <Badge value="in use">in use</Badge>}
                <button type="button" className="text-xs font-semibold text-sky-300" onClick={async () => setOpen(await tr.template(t.id))}>
                  Open →
                </button>
              </li>
            ))}
          {!templates.length && <li className="py-2 text-sm text-slate-500">No templates yet. Create a pre and a post template, then link them to a program version.</li>}
        </ul>
      </Card>
    </div>
  );
}
