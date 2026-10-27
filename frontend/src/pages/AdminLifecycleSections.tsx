import { useEffect, useRef, useState } from "react";
import {
  createAssignmentRule,
  createCustomField,
  deactivateAssignmentRule,
  deactivateCustomField,
  deleteAssignmentRule,
  deleteCustomField,
  intentCatalog,
  listAssignees,
  listAssignmentRules,
  listCustomFields,
  listPositions,
  listRoles,
  listStateModels,
  resetStateModel,
  saveStateModel,
} from "../api/client";
import { ASSIGNMENT_STRATEGIES, CUSTOM_FIELD_DATA_TYPES, ENTITY_LABELS, ENTITY_TYPES } from "../api/types";
import type {
  AppRole,
  Assignees,
  AssignmentRule,
  CustomFieldDefinition,
  EntityType,
  LifecycleState,
  LifecycleTransition,
  Position,
  StateModelConfig,
} from "../api/types";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";
const CELL_INPUT = "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200";
const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300";

function errorText(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback;
}

function EntityTypeSelect({ value, onChange }: { value: string; onChange: (v: EntityType) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as EntityType)} className={INPUT}>
      {ENTITY_TYPES.map((t) => (
        <option key={t} value={t}>
          {ENTITY_LABELS[t]}
        </option>
      ))}
    </select>
  );
}

const ACCESS_LEVELS = ["admin", "agent", "viewer"];

/** Who may run a transition: a dropdown of the access levels and the tenant's
 * roles, stored as the comma-separated names the backend checks. */
function RolesPicker({ value, roles, onChange }: { value: string; roles: AppRole[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const picked = value.split(",").map((r) => r.trim()).filter(Boolean);
  const has = (name: string) => picked.some((p) => p.toLowerCase() === name.toLowerCase());
  const known = new Set([...ACCESS_LEVELS, ...roles.map((r) => r.name.toLowerCase())]);
  const unknown = picked.filter((p) => !known.has(p.toLowerCase()));

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  function toggle(name: string) {
    onChange((has(name) ? picked.filter((p) => p.toLowerCase() !== name.toLowerCase()) : [...picked, name]).join(","));
  }

  const option = (name: string, hint?: string, warn = false) => (
    <label key={name} className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-slate-800">
      <input type="checkbox" checked={has(name)} onChange={() => toggle(name)} className="accent-sky-500" />
      <span className={warn ? "text-amber-300" : "text-slate-200"}>{name}</span>
      {hint && <span className={`ml-auto text-[10px] ${warn ? "text-amber-400" : "text-slate-500"}`}>{hint}</span>}
    </label>
  );

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`${CELL_INPUT} flex items-center gap-1 text-left`}
        title={picked.length ? picked.join(", ") : "Everyone with admin or agent access"}
      >
        <span className={`min-w-0 flex-1 truncate ${picked.length ? "" : "text-slate-500"}`}>{picked.length ? picked.join(", ") : "Everyone"}</span>
        <span className="text-slate-500">▾</span>
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 max-h-72 w-60 overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 p-1.5 text-xs shadow-xl shadow-black/40">
          <p className="px-2 pb-1 pt-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Roles</p>
          {roles.filter((r) => !r.name.includes(",")).map((r) => option(r.name, r.is_active ? undefined : "switched off"))}
          {roles.length === 0 && <p className="px-2 py-1 text-slate-500">No roles yet - add them under Roles.</p>}
          <p className="mt-1 border-t border-slate-800 px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Access level</p>
          {ACCESS_LEVELS.map((l) => option(l))}
          {unknown.length > 0 && (
            <>
              <p className="mt-1 border-t border-slate-800 px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-amber-500">Not a role - untick to save</p>
              {unknown.map((u) => option(u, "unknown", true))}
            </>
          )}
          <button type="button" onClick={() => onChange("")} className="mt-1 w-full rounded border-t border-slate-800 px-2 py-1.5 text-left text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            Clear - everyone (admin, agent)
          </button>
        </div>
      )}
    </div>
  );
}

// ---------- Lifecycle state models ----------

export function StateModelsSection() {
  const [models, setModels] = useState<StateModelConfig[]>([]);
  const [entityType, setEntityType] = useState<EntityType>("quote");
  const [name, setName] = useState("");
  const [states, setStates] = useState<LifecycleState[]>([]);
  const [transitions, setTransitions] = useState<LifecycleTransition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // Actions that can own a transition - by name; the code stays internal.
  const [actions, setActions] = useState<{ code: string; label: string }[]>([]);

  const [roles, setRoles] = useState<AppRole[]>([]);

  useEffect(() => {
    intentCatalog()
      .then((c) => setActions(c.intents.filter((i) => i.kind === "builtin").map((i) => ({ code: i.code, label: i.label }))))
      .catch(() => setActions([]));
    listRoles().then(setRoles).catch(() => setRoles([]));
  }, []);

  function edit(list: StateModelConfig[], type: EntityType) {
    const model = list.find((m) => m.entity_type === type);
    if (!model) return;
    setName(model.name);
    setStates(model.states.map((s) => ({ ...s })));
    setTransitions(model.transitions.map((t) => ({ ...t })));
    setError(null);
    setSaved(false);
  }

  function load(type: EntityType = entityType) {
    listStateModels().then((list) => {
      setModels(list);
      edit(list, type);
    });
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => load(), []);

  const current = models.find((m) => m.entity_type === entityType);
  const stateCodes = states.map((s) => s.code).filter(Boolean);

  function patchState(i: number, patch: Partial<LifecycleState>) {
    setStates((list) => list.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  }

  function patchTransition(i: number, patch: Partial<LifecycleTransition>) {
    setTransitions((list) => list.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }

  async function save() {
    setError(null);
    setSaved(false);
    try {
      await saveStateModel(entityType, {
        name: name.trim(),
        states: states.map(({ code, label, editable, is_terminal }) => ({ code: code.trim(), label, editable, is_terminal })),
        transitions: transitions.map((t) => ({ ...t, intent_type: t.intent_type || null })),
      });
      load();
      setSaved(true);
    } catch (e) {
      setError(errorText(e, "Failed to save state model."));
    }
  }

  async function reset() {
    setError(null);
    try {
      await resetStateModel(entityType);
      load();
    } catch (e) {
      setError(errorText(e, "Failed to reset state model."));
    }
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Each record type moves through a state model. Built-in states mirror the record's real status and can't be removed;
        add custom sub-states, decide which states are editable (non-editable = locked, changes need a revision), and which
        actions move between them. A transition done by an action runs only through that action (so its business steps run);
        the rest run as "Move to another stage". Leave allowed roles blank for everyone.
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <label className="w-56 text-xs text-slate-400">
          Record type
          <EntityTypeSelect
            value={entityType}
            onChange={(t) => {
              setEntityType(t);
              edit(models, t);
            }}
          />
        </label>
        <label className="flex-1 text-xs text-slate-400">
          Model name
          <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT} />
        </label>
        {current && (
          <span className={`mb-2 rounded-md border px-2 py-0.5 text-xs ${current.configured ? "border-sky-500/40 text-sky-400" : "border-slate-700 text-slate-500"}`}>
            {current.configured ? "configured" : "default"}
          </span>
        )}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-3 text-base font-bold text-slate-100">States</h3>
        <div className="grid grid-cols-[1fr_1.4fr_auto_auto_auto] items-center gap-x-3 gap-y-2 text-xs">
          <span className="text-slate-500">Code</span>
          <span className="text-slate-500">Label</span>
          <span className="text-slate-500">Editable</span>
          <span className="text-slate-500">Terminal</span>
          <span />
          {states.map((s, i) => (
            <div key={i} className="contents">
              {s.built_in ? (
                <span className="font-mono text-slate-300">
                  {s.code} <span className="text-slate-600">built-in</span>
                </span>
              ) : (
                <input value={s.code} onChange={(e) => patchState(i, { code: e.target.value })} placeholder="legal_review" className={CELL_INPUT} />
              )}
              <input value={s.label} onChange={(e) => patchState(i, { label: e.target.value })} className={CELL_INPUT} />
              <input type="checkbox" checked={s.editable} onChange={(e) => patchState(i, { editable: e.target.checked })} />
              <input type="checkbox" checked={s.is_terminal} onChange={(e) => patchState(i, { is_terminal: e.target.checked })} />
              {s.built_in ? (
                <span />
              ) : (
                <button onClick={() => setStates((list) => list.filter((_, j) => j !== i))} className="text-slate-500 hover:text-red-400">
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>
        <button
          onClick={() => setStates((list) => [...list, { code: "", label: "", editable: true, is_terminal: false }])}
          className={`mt-3 ${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}
        >
          + Custom state
        </button>
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-3 text-base font-bold text-slate-100">Transitions</h3>
        <div className="grid grid-cols-[1fr_1fr_1fr_0.8fr_1fr_auto] items-center gap-x-2 gap-y-2 text-xs">
          <span className="text-slate-500">From</span>
          <span className="text-slate-500">To</span>
          <span className="text-slate-500">Action</span>
          <span className="text-slate-500">Done by</span>
          <span className="text-slate-500">Allowed roles</span>
          <span />
          {transitions.map((t, i) => (
            <div key={i} className="contents">
              <select value={t.from_state} onChange={(e) => patchTransition(i, { from_state: e.target.value })} className={CELL_INPUT}>
                <option value="*">* (any)</option>
                {stateCodes.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select value={t.to_state} onChange={(e) => patchTransition(i, { to_state: e.target.value })} className={CELL_INPUT}>
                {stateCodes.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <input value={t.action} onChange={(e) => patchTransition(i, { action: e.target.value })} placeholder="send_to_legal" className={CELL_INPUT} />
              <select value={t.intent_type ?? ""} onChange={(e) => patchTransition(i, { intent_type: e.target.value || null })} className={CELL_INPUT}>
                <option value="">Move to another stage</option>
                {t.intent_type && !actions.some((a) => a.code === t.intent_type) && <option value={t.intent_type}>a standard action</option>}
                {actions.map((a) => (
                  <option key={a.code} value={a.code}>
                    {a.label}
                  </option>
                ))}
              </select>
              <RolesPicker value={t.allowed_roles} roles={roles} onChange={(v) => patchTransition(i, { allowed_roles: v })} />
              <button onClick={() => setTransitions((list) => list.filter((_, j) => j !== i))} className="text-slate-500 hover:text-red-400">
                ✕
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={() =>
            setTransitions((list) => [
              ...list,
              { from_state: stateCodes[0] ?? "", to_state: stateCodes[0] ?? "", action: "", intent_type: null, allowed_roles: "" },
            ])
          }
          className={`mt-3 ${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}
        >
          + Transition
        </button>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
      {saved && <p className="text-xs text-emerald-400">Saved.</p>}
      <div className="flex gap-2">
        <button onClick={save} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Save State Model
        </button>
        {current?.configured && (
          <button onClick={reset} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-bold text-slate-300 hover:border-amber-500 hover:text-amber-400">
            Reset to Default
          </button>
        )}
      </div>
    </div>
  );
}

// ---------- Positions + assignment rules ----------

function emptyRuleForm() {
  return { name: "", entityType: "quote" as EntityType, onState: "", strategy: "position", positionId: "", userId: "", departmentId: "", priority: "100" };
}

export function AssignmentSection() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [rules, setRules] = useState<AssignmentRule[]>([]);
  const [assignees, setAssignees] = useState<Assignees>({ users: [], positions: [] });
  const [ruleForm, setRuleForm] = useState(emptyRuleForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listPositions().then(setPositions);
    listAssignmentRules().then(setRules);
    listAssignees().then(setAssignees);
  }

  useEffect(load, []);

  const positionName = (id: number | null) => positions.find((p) => p.id === id)?.name ?? `#${id}`;
  const userName = (id: number | null) => assignees.users.find((u) => u.id === id)?.name ?? `#${id}`;

  async function run(action: () => Promise<unknown>, fallback: string) {
    setError(null);
    try {
      await action();
      load();
      return true;
    } catch (e) {
      setError(errorText(e, fallback));
      return false;
    }
  }

  async function addRule() {
    if (!ruleForm.name.trim()) {
      setError("Rule name is required.");
      return;
    }
    const ok = await run(
      () =>
        createAssignmentRule({
          name: ruleForm.name.trim(),
          entity_type: ruleForm.entityType,
          on_state: ruleForm.onState.trim() || null,
          strategy: ruleForm.strategy,
          position_id: !["user", "department"].includes(ruleForm.strategy) && ruleForm.positionId ? Number(ruleForm.positionId) : null,
          user_id: ruleForm.strategy === "user" && ruleForm.userId ? Number(ruleForm.userId) : null,
          department_id: ruleForm.strategy === "department" && ruleForm.departmentId ? Number(ruleForm.departmentId) : null,
          priority: Number(ruleForm.priority) || 100,
        }),
      "Failed to create rule."
    );
    if (ok) setRuleForm(emptyRuleForm());
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Assignment rules route records automatically when they are created (no "on state") or when they enter a state;
        the lowest priority number wins. A record owned by a position appears in My Work for everyone holding it and
        counts as that position's data. Positions themselves are managed under Org Structure.
      </p>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="space-y-2">
        <h3 className="text-base font-bold text-slate-100">Assignment Rules</h3>
        {rules.map((r) => (
          <div key={r.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${r.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="font-mono text-xs text-slate-500">p{r.priority}</span>
            <span className="font-semibold text-slate-200">{r.name}</span>
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">{r.entity_type}</span>
            <span className="text-xs text-slate-400">{r.on_state ? `on entering ${r.on_state}` : "on create"}</span>
            <span className="text-xs text-violet-400">
              {r.strategy} →{" "}
              {r.strategy === "user" ? userName(r.user_id) : r.strategy === "department"
                ? `${(assignees.departments ?? []).find((d) => d.id === r.department_id)?.name ?? `#${r.department_id}`} department`
                : positionName(r.position_id)}
            </span>
            {!r.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
            <div className="ml-auto flex gap-2">
              {r.is_active && (
                <button onClick={() => run(() => deactivateAssignmentRule(r.id), "Failed to deactivate rule.")} className={`${SMALL_BTN} hover:border-amber-500 hover:text-amber-400`}>
                  Deactivate
                </button>
              )}
              <button onClick={() => run(() => deleteAssignmentRule(r.id), "Failed to delete rule.")} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>
                Delete
              </button>
            </div>
          </div>
        ))}
        {rules.length === 0 && <p className="text-sm text-slate-500">No assignment rules - records stay with whoever created them.</p>}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">New Assignment Rule</h3>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-xs text-slate-400">
            Name
            <input value={ruleForm.name} onChange={(e) => setRuleForm((f) => ({ ...f, name: e.target.value }))} placeholder="New quotes round robin" className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            Record type
            <EntityTypeSelect value={ruleForm.entityType} onChange={(t) => setRuleForm((f) => ({ ...f, entityType: t }))} />
          </label>
          <label className="text-xs text-slate-400">
            On entering state (blank = on create)
            <input value={ruleForm.onState} onChange={(e) => setRuleForm((f) => ({ ...f, onState: e.target.value }))} placeholder="e.g. approved" className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            Strategy
            <select value={ruleForm.strategy} onChange={(e) => setRuleForm((f) => ({ ...f, strategy: e.target.value }))} className={INPUT}>
              {ASSIGNMENT_STRATEGIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          {ruleForm.strategy === "department" ? (
            <label className="text-xs text-slate-400">
              Department
              <select value={ruleForm.departmentId} onChange={(e) => setRuleForm((f) => ({ ...f, departmentId: e.target.value }))} className={INPUT}>
                <option value="">—</option>
                {(assignees.departments ?? []).map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
          ) : ruleForm.strategy === "user" ? (
            <label className="text-xs text-slate-400">
              User
              <select value={ruleForm.userId} onChange={(e) => setRuleForm((f) => ({ ...f, userId: e.target.value }))} className={INPUT}>
                <option value="">—</option>
                {assignees.users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className="text-xs text-slate-400">
              Position
              <select value={ruleForm.positionId} onChange={(e) => setRuleForm((f) => ({ ...f, positionId: e.target.value }))} className={INPUT}>
                <option value="">—</option>
                {positions.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="text-xs text-slate-400">
            Priority
            <input type="number" value={ruleForm.priority} onChange={(e) => setRuleForm((f) => ({ ...f, priority: e.target.value }))} className={INPUT} />
          </label>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          position = the position owns it · round_robin = rotate across the position's members · user = a named user · department = the
          whole department owns it (in every member's My Work).
        </p>
        <button onClick={addRule} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create Rule
        </button>
      </div>
    </div>
  );
}

// ---------- Custom fields ----------

function emptyFieldForm() {
  return { entityType: "quote" as EntityType, key: "", label: "", dataType: "text", allowedValues: "", required: false, defaultValue: "" };
}

export function CustomFieldsSection() {
  const [fields, setFields] = useState<CustomFieldDefinition[]>([]);
  const [filter, setFilter] = useState<EntityType | "">("");
  const [form, setForm] = useState(emptyFieldForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listCustomFields(filter || undefined).then(setFields);
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [filter]);

  async function run(action: () => Promise<unknown>, fallback: string) {
    setError(null);
    try {
      await action();
      load();
      return true;
    } catch (e) {
      setError(errorText(e, fallback));
      return false;
    }
  }

  async function submit() {
    if (!form.key.trim() || !form.label.trim()) {
      setError("Key and label are required.");
      return;
    }
    if (form.dataType === "picklist" && !form.allowedValues.trim()) {
      setError("A picklist needs allowed values (comma-separated).");
      return;
    }
    const ok = await run(
      () =>
        createCustomField({
          entity_type: form.entityType,
          key: form.key.trim(),
          label: form.label.trim(),
          data_type: form.dataType,
          allowed_values: form.dataType === "picklist" ? form.allowedValues.trim() : null,
          required: form.required,
          default_value: form.defaultValue.trim() || null,
        }),
      "Failed to create field."
    );
    if (ok) setForm(emptyFieldForm());
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Extra fields on any record type, set through "Update a record" or the record panel's Edit form, and
        readable by flows as <span className="font-mono">record.&lt;key&gt;</span>. Values follow the record's lock: once a
        state is non-editable, custom fields lock too.
      </p>

      <label className="block w-56 text-xs text-slate-400">
        Show
        <select value={filter} onChange={(e) => setFilter(e.target.value as EntityType | "")} className={INPUT}>
          <option value="">All record types</option>
          {ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {ENTITY_LABELS[t]}
            </option>
          ))}
        </select>
      </label>

      <div className="space-y-2">
        {fields.map((f) => (
          <div key={f.id} className={`flex flex-wrap items-center gap-3 rounded-lg border px-4 py-3 text-sm ${f.is_active ? "border-slate-700 bg-slate-900/60" : "border-slate-800 bg-slate-950/60 opacity-60"}`}>
            <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-bold text-slate-300">{f.entity_type}</span>
            <span className="font-semibold text-slate-200">{f.label}</span>
            <span className="font-mono text-xs text-violet-400">{f.key}</span>
            <span className="text-xs text-slate-400">
              {f.data_type}
              {f.allowed_values && `: ${f.allowed_values}`}
            </span>
            {f.required && <span className="text-xs text-amber-400">required</span>}
            {f.default_value && <span className="text-xs text-slate-500">default {f.default_value}</span>}
            {!f.is_active && <span className="rounded-md border border-slate-700 px-2 py-0.5 text-xs text-slate-500">inactive</span>}
            <div className="ml-auto flex gap-2">
              {f.is_active && (
                <button onClick={() => run(() => deactivateCustomField(f.id), "Failed to deactivate field.")} className={`${SMALL_BTN} hover:border-amber-500 hover:text-amber-400`}>
                  Deactivate
                </button>
              )}
              <button onClick={() => run(() => deleteCustomField(f.id), "Failed to delete field.")} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>
                Delete
              </button>
            </div>
          </div>
        ))}
        {fields.length === 0 && <p className="text-sm text-slate-500">No custom fields yet.</p>}
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-4 text-base font-bold text-slate-100">New Custom Field</h3>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-xs text-slate-400">
            Record type
            <EntityTypeSelect value={form.entityType} onChange={(t) => setForm((f) => ({ ...f, entityType: t }))} />
          </label>
          <label className="text-xs text-slate-400">
            Key (lowercase_snake)
            <input value={form.key} onChange={(e) => setForm((f) => ({ ...f, key: e.target.value }))} placeholder="contract_term" className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            Label
            <input value={form.label} onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))} placeholder="Contract term" className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            Data type
            <select value={form.dataType} onChange={(e) => setForm((f) => ({ ...f, dataType: e.target.value }))} className={INPUT}>
              {CUSTOM_FIELD_DATA_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          {form.dataType === "picklist" && (
            <label className="text-xs text-slate-400">
              Allowed values (comma-separated)
              <input value={form.allowedValues} onChange={(e) => setForm((f) => ({ ...f, allowedValues: e.target.value }))} placeholder="12m,24m,36m" className={INPUT} />
            </label>
          )}
          <label className="text-xs text-slate-400">
            Default value
            <input value={form.defaultValue} onChange={(e) => setForm((f) => ({ ...f, defaultValue: e.target.value }))} className={INPUT} />
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-400">
            <input type="checkbox" checked={form.required} onChange={(e) => setForm((f) => ({ ...f, required: e.target.checked }))} />
            Required
          </label>
        </div>
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
          Create Field
        </button>
      </div>
    </div>
  );
}
