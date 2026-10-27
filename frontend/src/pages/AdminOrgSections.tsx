import { useEffect, useState } from "react";
import {
  accessCatalog,
  createDepartment,
  createPosition,
  createRole,
  deleteDepartment,
  deletePosition,
  deleteRole,
  getAccessPolicy,
  listDepartments,
  listPositions,
  listRoles,
  listUserAccess,
  setAccessPolicy,
  setUserAccess,
  updateDepartment,
  updatePosition,
  updateRole,
} from "../api/client";
import type { AccessCatalog, AccessPolicy, AppRole, Department, Position, UserAccess } from "../api/types";

const INPUT = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";
const CELL = "w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200";
const SMALL_BTN = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-bold text-slate-300";
const PRIMARY_BTN = "rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50";

const SCOPE_STYLES: Record<string, string> = {
  own: "border-slate-600 text-slate-300",
  team: "border-sky-500/40 text-sky-300",
  department: "border-violet-500/40 text-violet-300",
  department_tree: "border-fuchsia-500/40 text-fuchsia-300",
  org: "border-emerald-500/40 text-emerald-300",
};

function errorText(e: unknown, fallback: string) {
  return e instanceof Error ? e.message.replace(/^\d+ [^:]*: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1") : fallback;
}

function useRunner(reload: () => void) {
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>, fallback: string) {
    setError(null);
    try {
      await action();
      reload();
      return true;
    } catch (e) {
      setError(errorText(e, fallback));
      return false;
    }
  }
  return { error, setError, run };
}

/** Departments ordered as a tree, with their depth for indentation. */
function departmentTree(departments: Department[]): { dept: Department; depth: number }[] {
  const out: { dept: Department; depth: number }[] = [];
  const visit = (parent: number | null, depth: number) => {
    departments
      .filter((d) => d.parent_department_id === parent && d.is_active)
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach((d) => {
        out.push({ dept: d, depth });
        visit(d.id, depth + 1);
      });
  };
  visit(null, 0);
  return out;
}

// ---------------------------------------------------------------- org structure

function OrgChartNode({
  position,
  positions,
  departments,
  scopeLabels,
}: {
  position: Position;
  positions: Position[];
  departments: Map<number, string>;
  scopeLabels: Map<string, string>;
}) {
  const reports = positions.filter((p) => p.parent_position_id === position.id && p.is_active);
  return (
    <li className="space-y-2">
      <div className="inline-block min-w-56 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-slate-100">{position.name}</span>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] ${SCOPE_STYLES[position.data_scope] ?? ""}`}>
            {scopeLabels.get(position.data_scope) ?? position.data_scope}
          </span>
        </div>
        <div className="text-xs text-slate-500">{position.department_id ? departments.get(position.department_id) : "no department"}</div>
        <div className="mt-1 text-xs text-slate-300">
          {position.members.length ? position.members.map((m) => m.name + (m.is_primary ? "" : " (secondary)")).join(", ") : "vacant"}
        </div>
      </div>
      {reports.length > 0 && (
        <ul className="ml-6 space-y-2 border-l border-slate-700 pl-4">
          {reports.map((r) => (
            <OrgChartNode key={r.id} position={r} positions={positions} departments={departments} scopeLabels={scopeLabels} />
          ))}
        </ul>
      )}
    </li>
  );
}

type PositionDraft = { name: string; departmentId: string; parentId: string; scope: string };

function emptyPositionDraft(): PositionDraft {
  return { name: "", departmentId: "", parentId: "", scope: "own" };
}

export function OrgStructureSection() {
  const [catalog, setCatalog] = useState<AccessCatalog | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [deptForm, setDeptForm] = useState({ name: "", parentId: "" });
  const [editingDept, setEditingDept] = useState<{ id: number; name: string; parentId: string } | null>(null);
  const [positionForm, setPositionForm] = useState<PositionDraft>(emptyPositionDraft());
  const [editingPosition, setEditingPosition] = useState<(PositionDraft & { id: number }) | null>(null);

  function load() {
    listDepartments().then(setDepartments);
    listPositions().then(setPositions);
  }

  useEffect(() => {
    accessCatalog().then(setCatalog);
    load();
  }, []);

  const { error, setError, run } = useRunner(load);
  const tree = departmentTree(departments);
  const deptNames = new Map(departments.map((d) => [d.id, d.name]));
  const scopeLabels = new Map((catalog?.data_scopes ?? []).map((s) => [s.key, s.label]));
  const activePositions = positions.filter((p) => p.is_active);
  const roots = activePositions.filter((p) => !p.parent_position_id || !activePositions.some((q) => q.id === p.parent_position_id));
  const asNum = (v: string) => (v ? Number(v) : null);

  async function addDepartment() {
    if (!deptForm.name.trim()) return setError("Department name is required.");
    if (await run(() => createDepartment({ name: deptForm.name.trim(), parent_department_id: asNum(deptForm.parentId) }), "Failed to create department.")) {
      setDeptForm({ name: "", parentId: "" });
    }
  }

  async function saveDepartment() {
    if (!editingDept) return;
    const ok = await run(
      () => updateDepartment(editingDept.id, { name: editingDept.name.trim(), parent_department_id: asNum(editingDept.parentId) }),
      "Failed to update department."
    );
    if (ok) setEditingDept(null);
  }

  async function addPosition() {
    if (!positionForm.name.trim()) return setError("Position name is required.");
    const ok = await run(
      () =>
        createPosition({
          name: positionForm.name.trim(),
          department_id: asNum(positionForm.departmentId),
          parent_position_id: asNum(positionForm.parentId),
          data_scope: positionForm.scope,
          user_ids: [],
        }),
      "Failed to create position."
    );
    if (ok) setPositionForm(emptyPositionDraft());
  }

  async function savePosition() {
    if (!editingPosition) return;
    const ok = await run(
      () =>
        updatePosition(editingPosition.id, {
          name: editingPosition.name.trim(),
          department_id: asNum(editingPosition.departmentId),
          parent_position_id: asNum(editingPosition.parentId),
          data_scope: editingPosition.scope,
        }),
      "Failed to update position."
    );
    if (ok) setEditingPosition(null);
  }

  const deptOptions = (exclude?: number) => (
    <>
      <option value="">—</option>
      {tree
        .filter(({ dept }) => dept.id !== exclude)
        .map(({ dept, depth }) => (
          <option key={dept.id} value={dept.id}>
            {"  ".repeat(depth)}
            {dept.name}
          </option>
        ))}
    </>
  );
  const positionOptions = (exclude?: number) => (
    <>
      <option value="">— (top of the org)</option>
      {activePositions
        .filter((p) => p.id !== exclude)
        .map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
    </>
  );
  const scopeOptions = (catalog?.data_scopes ?? []).map((s) => (
    <option key={s.key} value={s.key}>
      {s.label}
    </option>
  ));

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Organization → Departments → Positions → Users. A position sits in a department, reports to another position, and has
        a <b>data scope</b> - the widest set of records its holders see on every page: <i>My data</i> (records they created or
        own), <i>My team</i> (plus everyone reporting into them), <i>My department</i>, <i>All departments</i> (their
        department and its sub-departments) or the <i>Whole organization</i>. Put people into positions under{" "}
        <b>Roles &amp; Access</b>.
      </p>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</p>}

      <div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]">
        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
          <h3 className="mb-3 text-base font-bold text-slate-100">Departments</h3>
          <div className="space-y-1">
            {tree.map(({ dept, depth }) =>
              editingDept?.id === dept.id ? (
                <div key={dept.id} className="flex flex-wrap items-center gap-2 rounded-md bg-slate-950 p-2">
                  <input value={editingDept.name} onChange={(e) => setEditingDept({ ...editingDept, name: e.target.value })} className={`${CELL} flex-1`} />
                  <select value={editingDept.parentId} onChange={(e) => setEditingDept({ ...editingDept, parentId: e.target.value })} className={`${CELL} w-36`}>
                    {deptOptions(dept.id)}
                  </select>
                  <button onClick={saveDepartment} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
                    Save
                  </button>
                  <button onClick={() => setEditingDept(null)} className={SMALL_BTN}>
                    ✕
                  </button>
                </div>
              ) : (
                <div key={dept.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-slate-800/60" style={{ paddingLeft: 8 + depth * 18 }}>
                  <span className="text-slate-500">{depth ? "└" : "▸"}</span>
                  <span className="text-slate-200">{dept.name}</span>
                  <span className="text-xs text-slate-500">{activePositions.filter((p) => p.department_id === dept.id).length} positions</span>
                  <div className="ml-auto flex gap-1">
                    <button
                      onClick={() => setEditingDept({ id: dept.id, name: dept.name, parentId: dept.parent_department_id ? String(dept.parent_department_id) : "" })}
                      className="px-1 text-xs text-slate-500 hover:text-sky-400"
                    >
                      edit
                    </button>
                    <button onClick={() => run(() => deleteDepartment(dept.id), "Failed to delete department.")} className="px-1 text-xs text-slate-500 hover:text-red-400">
                      delete
                    </button>
                  </div>
                </div>
              )
            )}
            {tree.length === 0 && <p className="text-sm text-slate-500">No departments yet.</p>}
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="text-xs text-slate-400">
              New department
              <input value={deptForm.name} onChange={(e) => setDeptForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Enterprise Sales" className={INPUT} />
            </label>
            <label className="text-xs text-slate-400">
              Under
              <select value={deptForm.parentId} onChange={(e) => setDeptForm((f) => ({ ...f, parentId: e.target.value }))} className={INPUT}>
                {deptOptions()}
              </select>
            </label>
            <button onClick={addDepartment} className={PRIMARY_BTN}>
              Add
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
          <h3 className="mb-3 text-base font-bold text-slate-100">Org chart</h3>
          {roots.length ? (
            <ul className="space-y-3">
              {roots.map((p) => (
                <OrgChartNode key={p.id} position={p} positions={activePositions} departments={deptNames} scopeLabels={scopeLabels} />
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">Add positions to see the reporting lines.</p>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-3 text-base font-bold text-slate-100">Positions</h3>
        <div className="grid grid-cols-[1.3fr_1fr_1fr_1.2fr_auto] items-center gap-x-3 gap-y-2 text-xs">
          <span className="text-slate-500">Position</span>
          <span className="text-slate-500">Department</span>
          <span className="text-slate-500">Reports to</span>
          <span className="text-slate-500">Data scope</span>
          <span />
          {activePositions.map((p) =>
            editingPosition?.id === p.id ? (
              <div key={p.id} className="contents">
                <input value={editingPosition.name} onChange={(e) => setEditingPosition({ ...editingPosition, name: e.target.value })} className={CELL} />
                <select value={editingPosition.departmentId} onChange={(e) => setEditingPosition({ ...editingPosition, departmentId: e.target.value })} className={CELL}>
                  {deptOptions()}
                </select>
                <select value={editingPosition.parentId} onChange={(e) => setEditingPosition({ ...editingPosition, parentId: e.target.value })} className={CELL}>
                  {positionOptions(p.id)}
                </select>
                <select value={editingPosition.scope} onChange={(e) => setEditingPosition({ ...editingPosition, scope: e.target.value })} className={CELL}>
                  {scopeOptions}
                </select>
                <div className="flex gap-1">
                  <button onClick={savePosition} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
                    Save
                  </button>
                  <button onClick={() => setEditingPosition(null)} className={SMALL_BTN}>
                    ✕
                  </button>
                </div>
              </div>
            ) : (
              <div key={p.id} className="contents">
                <span className="text-sm text-slate-200">
                  {p.name}
                  <span className="ml-2 text-xs text-slate-500">{p.members.length ? `${p.members.length} holder(s)` : "vacant"}</span>
                </span>
                <span className="text-slate-300">{p.department_id ? deptNames.get(p.department_id) : "—"}</span>
                <span className="text-slate-300">{activePositions.find((q) => q.id === p.parent_position_id)?.name ?? "—"}</span>
                <span>
                  <span className={`rounded border px-1.5 py-0.5 ${SCOPE_STYLES[p.data_scope] ?? ""}`}>{scopeLabels.get(p.data_scope) ?? p.data_scope}</span>
                </span>
                <div className="flex gap-1">
                  <button
                    onClick={() =>
                      setEditingPosition({
                        id: p.id,
                        name: p.name,
                        departmentId: p.department_id ? String(p.department_id) : "",
                        parentId: p.parent_position_id ? String(p.parent_position_id) : "",
                        scope: p.data_scope,
                      })
                    }
                    className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}
                  >
                    Edit
                  </button>
                  <button onClick={() => run(() => deletePosition(p.id), "Failed to delete position.")} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>
                    Delete
                  </button>
                </div>
              </div>
            )
          )}
        </div>
        {activePositions.length === 0 && <p className="text-sm text-slate-500">No positions yet.</p>}

        <div className="mt-5 grid gap-3 border-t border-slate-800 pt-4 md:grid-cols-[1.3fr_1fr_1fr_1.2fr_auto] md:items-end">
          <label className="text-xs text-slate-400">
            New position
            <input value={positionForm.name} onChange={(e) => setPositionForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Sales Executive - North" className={INPUT} />
          </label>
          <label className="text-xs text-slate-400">
            Department
            <select value={positionForm.departmentId} onChange={(e) => setPositionForm((f) => ({ ...f, departmentId: e.target.value }))} className={INPUT}>
              {deptOptions()}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Reports to
            <select value={positionForm.parentId} onChange={(e) => setPositionForm((f) => ({ ...f, parentId: e.target.value }))} className={INPUT}>
              {positionOptions()}
            </select>
          </label>
          <label className="text-xs text-slate-400">
            Data scope
            <select value={positionForm.scope} onChange={(e) => setPositionForm((f) => ({ ...f, scope: e.target.value }))} className={INPUT}>
              {scopeOptions}
            </select>
          </label>
          <button onClick={addPosition} className={PRIMARY_BTN}>
            Add
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- roles & access

function emptyRoleForm() {
  return { name: "", description: "", pages: [] as string[] };
}

function PageChecklist({ pages, value, onChange }: { pages: { key: string; label: string }[]; value: string[]; onChange: (pages: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {pages.map((p) => {
        const checked = value.includes(p.key);
        return (
          <label
            key={p.key}
            className={`flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${checked ? "border-sky-500/50 bg-sky-500/10 text-sky-300" : "border-slate-700 text-slate-400"}`}
          >
            <input type="checkbox" checked={checked} onChange={() => onChange(checked ? value.filter((v) => v !== p.key) : [...value, p.key])} />
            {p.label}
          </label>
        );
      })}
    </div>
  );
}

type AccessDraft = { roleIds: number[]; positionIds: number[]; primaryId: number | null };

export function RolesAccessSection({ onAccessChanged }: { onAccessChanged?: () => void }) {
  const [catalog, setCatalog] = useState<AccessCatalog | null>(null);
  const [roles, setRoles] = useState<AppRole[]>([]);
  const [users, setUsers] = useState<UserAccess[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [roleForm, setRoleForm] = useState(emptyRoleForm());
  const [editingRole, setEditingRole] = useState<(ReturnType<typeof emptyRoleForm> & { id: number }) | null>(null);
  const [editingUser, setEditingUser] = useState<(AccessDraft & { id: number }) | null>(null);
  const [filter, setFilter] = useState("");
  const [policy, setPolicy] = useState<AccessPolicy | null>(null);

  function load() {
    getAccessPolicy().then(setPolicy).catch(() => setPolicy(null));
    listRoles().then(setRoles);
    listUserAccess().then(setUsers);
    listPositions().then(setPositions);
    listDepartments().then(setDepartments);
  }

  useEffect(() => {
    accessCatalog().then(setCatalog);
    load();
  }, []);

  const { error, setError, run } = useRunner(() => {
    load();
    onAccessChanged?.();
  });
  const pages = catalog?.pages ?? [];
  const pageLabel = new Map(pages.map((p) => [p.key, p.label]));
  const deptNames = new Map(departments.map((d) => [d.id, d.name]));
  const activePositions = positions.filter((p) => p.is_active);

  async function addRole() {
    if (!roleForm.name.trim()) return setError("Role name is required.");
    if (await run(() => createRole({ ...roleForm, name: roleForm.name.trim() }), "Failed to create role.")) setRoleForm(emptyRoleForm());
  }

  async function saveRole() {
    if (!editingRole) return;
    if (await run(() => updateRole(editingRole.id, { name: editingRole.name.trim(), description: editingRole.description, pages: editingRole.pages }), "Failed to update role."))
      setEditingRole(null);
  }

  async function saveUser() {
    if (!editingUser) return;
    const target = users.find((u) => u.id === editingUser.id);
    if (target && needsBoth(target)) {
      const missing = [editingUser.roleIds.length === 0 && "a role", editingUser.positionIds.length === 0 && "a position"].filter(Boolean);
      if (missing.length) return setError(`${target.name} needs ${missing.join(" and ")} - every user must hold at least one role and one position.`);
    }
    const ok = await run(
      () =>
        setUserAccess(editingUser.id, {
          role_ids: editingUser.roleIds,
          position_ids: editingUser.positionIds,
          primary_position_id: editingUser.primaryId,
        }),
      "Failed to save access."
    );
    if (ok) setEditingUser(null);
  }

  function togglePosition(id: number) {
    setEditingUser((u) => {
      if (!u) return u;
      const has = u.positionIds.includes(id);
      const positionIds = has ? u.positionIds.filter((p) => p !== id) : [...u.positionIds, id];
      const primaryId = has && u.primaryId === id ? (positionIds[0] ?? null) : u.primaryId ?? (has ? null : id);
      return { ...u, positionIds, primaryId };
    });
  }

  // Administrators keep their built-in access; switched-off users don't sign in.
  function needsBoth(u: UserAccess) {
    return u.role !== "admin" && u.is_active;
  }
  const incomplete = users.filter((u) => needsBoth(u) && (u.role_ids.length === 0 || u.position_ids.length === 0));
  const shown = users.filter((u) => !filter || `${u.name} ${u.email}`.toLowerCase().includes(filter.toLowerCase()));

  async function toggle(key: keyof AccessPolicy) {
    if (!policy) return;
    const next = !policy[key];
    if (key === "require_role_and_position" && next && incomplete.length) {
      return setError(`Give ${incomplete.map((u) => u.name).join(", ")} a role and a position first - otherwise they lose their pages or data.`);
    }
    await run(() => setAccessPolicy({ [key]: next }), "Failed to save the setting.");
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        <b>Roles decide pages</b>: a user sees the union of the pages (and intents) of all their roles. <b>Positions decide
        data</b>: the user opens the app as their primary position and can switch to any
        other position they hold from the header; the same page then returns that position's records. The Admin page also
        requires the built-in <i>admin</i> level.
      </p>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</p>}

      {policy && (
        <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/60 p-5">
          <h3 className="text-base font-bold text-slate-100">Access rules</h3>
          {(
            [
              ["require_role_and_position", "Require a role and a position",
                "Every user (other than administrators) must hold at least one role and one position. Until they do, a user without a role gets no pages or intents, and one without a position sees only their own records - never the whole organization."],
              ["strict_approvals", "Strict approvals",
                "Only the people in an approval step's position decide it. Nobody approves their own request, and a step whose position nobody holds waits for an administrator instead of being open to anyone."],
            ] as [keyof AccessPolicy, string, string][]
          ).map(([key, label, help]) => (
            <label key={key} className="flex items-start gap-3 text-sm">
              <input type="checkbox" className="mt-1" checked={policy[key]} onChange={() => toggle(key)} />
              <span>
                <span className="font-semibold text-slate-200">{label}</span>
                <span className="block text-xs text-slate-500">{help}</span>
              </span>
            </label>
          ))}
          {incomplete.length > 0 && (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
              <b>{incomplete.length} user(s) missing a role or a position:</b>{" "}
              {incomplete.map((u, i) => (
                <span key={u.id}>
                  {i > 0 && ", "}
                  <button
                    className="underline hover:text-amber-200"
                    onClick={() => setEditingUser({ id: u.id, roleIds: u.role_ids, positionIds: u.position_ids, primaryId: u.primary_position_id })}
                  >
                    {u.name}
                  </button>{" "}
                  ({[u.role_ids.length === 0 && "role", u.position_ids.length === 0 && "position"].filter(Boolean).join(" + ")})
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <h3 className="mb-3 text-base font-bold text-slate-100">Roles</h3>
        <div className="space-y-2">
          {roles.map((r) =>
            editingRole?.id === r.id ? (
              <div key={r.id} className="space-y-2 rounded-lg border border-sky-500/30 bg-slate-950 p-3">
                <div className="grid gap-2 md:grid-cols-2">
                  <input value={editingRole.name} onChange={(e) => setEditingRole({ ...editingRole, name: e.target.value })} className={CELL} />
                  <input value={editingRole.description} onChange={(e) => setEditingRole({ ...editingRole, description: e.target.value })} placeholder="Description" className={CELL} />
                </div>
                <PageChecklist pages={pages} value={editingRole.pages} onChange={(p) => setEditingRole({ ...editingRole, pages: p })} />
                <div className="flex gap-2">
                  <button onClick={saveRole} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
                    Save
                  </button>
                  <button onClick={() => setEditingRole(null)} className={SMALL_BTN}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div key={r.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-4 py-3 text-sm">
                <span className="font-semibold text-slate-200">{r.name}</span>
                <span className="text-xs text-slate-500">{r.user_count} user(s)</span>
                <div className="flex flex-wrap gap-1">
                  {r.pages.map((p) => (
                    <span key={p} className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">
                      {pageLabel.get(p) ?? p}
                    </span>
                  ))}
                  {r.pages.length === 0 && <span className="text-xs text-amber-400">no pages</span>}
                </div>
                <div className="ml-auto flex gap-2">
                  <button
                    onClick={() => setEditingRole({ id: r.id, name: r.name, description: r.description, pages: r.pages })}
                    className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}
                  >
                    Edit
                  </button>
                  <button onClick={() => run(() => deleteRole(r.id), "Failed to delete role.")} className={`${SMALL_BTN} hover:border-red-500 hover:text-red-400`}>
                    Delete
                  </button>
                </div>
              </div>
            )
          )}
          {roles.length === 0 && <p className="text-sm text-slate-500">No roles yet - every user sees the default pages for their built-in level.</p>}
        </div>
        <div className="mt-4 space-y-2 border-t border-slate-800 pt-4">
          <div className="grid gap-2 md:grid-cols-2">
            <label className="text-xs text-slate-400">
              New role
              <input value={roleForm.name} onChange={(e) => setRoleForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Sales Executive" className={INPUT} />
            </label>
            <label className="text-xs text-slate-400">
              Description
              <input value={roleForm.description} onChange={(e) => setRoleForm((f) => ({ ...f, description: e.target.value }))} className={INPUT} />
            </label>
          </div>
          <PageChecklist pages={pages} value={roleForm.pages} onChange={(p) => setRoleForm((f) => ({ ...f, pages: p }))} />
          <button onClick={addRole} className={PRIMARY_BTN}>
            Create Role
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-base font-bold text-slate-100">Users</h3>
          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter users…" className="w-56 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-200" />
        </div>
        <div className="space-y-2">
          {shown.map((u) =>
            editingUser?.id === u.id ? (
              <div key={u.id} className="space-y-3 rounded-lg border border-sky-500/30 bg-slate-950 p-4">
                <div className="text-sm font-semibold text-slate-100">
                  {u.name} <span className="text-xs font-normal text-slate-500">{u.email}</span>
                </div>
                <div>
                  <p className="mb-1 text-xs font-bold text-slate-400">Roles (pages)</p>
                  <div className="flex flex-wrap gap-1.5">
                    {roles.map((r) => {
                      const checked = editingUser.roleIds.includes(r.id);
                      return (
                        <label key={r.id} className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${checked ? "border-sky-500/50 text-sky-300" : "border-slate-700 text-slate-400"}`}>
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() =>
                              setEditingUser({ ...editingUser, roleIds: checked ? editingUser.roleIds.filter((id) => id !== r.id) : [...editingUser.roleIds, r.id] })
                            }
                          />
                          {r.name}
                        </label>
                      );
                    })}
                    {roles.length === 0 && <span className="text-xs text-slate-500">Create roles above first.</span>}
                  </div>
                </div>
                <div>
                  <p className="mb-1 text-xs font-bold text-slate-400">Positions (data) - ● = primary</p>
                  <div className="flex flex-wrap gap-1.5">
                    {activePositions.map((p) => {
                      const checked = editingUser.positionIds.includes(p.id);
                      const primary = editingUser.primaryId === p.id;
                      return (
                        <span key={p.id} className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs ${checked ? "border-violet-500/50 text-violet-300" : "border-slate-700 text-slate-400"}`}>
                          <input type="checkbox" checked={checked} onChange={() => togglePosition(p.id)} />
                          {p.name}
                          {p.department_id && <span className="text-slate-500">· {deptNames.get(p.department_id)}</span>}
                          {checked && (
                            <button
                              onClick={() => setEditingUser({ ...editingUser, primaryId: p.id })}
                              title="Make primary"
                              className={primary ? "text-emerald-400" : "text-slate-600 hover:text-slate-300"}
                            >
                              {primary ? "●" : "○"}
                            </button>
                          )}
                        </span>
                      );
                    })}
                    {activePositions.length === 0 && <span className="text-xs text-slate-500">Create positions under Org Structure first.</span>}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button onClick={saveUser} className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}>
                    Save
                  </button>
                  <button onClick={() => setEditingUser(null)} className={SMALL_BTN}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div key={u.id} className={`flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 bg-slate-950 px-4 py-3 text-sm ${u.is_active ? "" : "opacity-50"}`}>
                <div className="min-w-48">
                  <div className="font-semibold text-slate-200">{u.name}</div>
                  <div className="text-xs text-slate-500">
                    {u.email} · {u.role}
                  </div>
                </div>
                <div className="flex flex-1 flex-wrap gap-1">
                  {u.role_ids.map((id) => (
                    <span key={id} className="rounded bg-sky-500/10 px-1.5 py-0.5 text-xs text-sky-300">
                      {roles.find((r) => r.id === id)?.name}
                    </span>
                  ))}
                  {u.position_ids.map((id) => (
                    <span key={id} className="rounded bg-violet-500/10 px-1.5 py-0.5 text-xs text-violet-300">
                      {id === u.primary_position_id ? "● " : ""}
                      {positions.find((p) => p.id === id)?.name}
                    </span>
                  ))}
                  {u.position_ids.length === 0 && (
                    <span className="text-xs text-amber-400">
                      no position - {policy?.require_role_and_position && u.role !== "admin" ? "sees only their own records" : "sees all data"}
                    </span>
                  )}
                  {u.role_ids.length === 0 && u.role !== "admin" && <span className="text-xs text-amber-400">no role</span>}
                </div>
                <span className="text-xs text-slate-500" title={u.pages.map((p) => pageLabel.get(p) ?? p).join(", ")}>
                  {u.pages.length} pages
                </span>
                <button
                  onClick={() => setEditingUser({ id: u.id, roleIds: u.role_ids, positionIds: u.position_ids, primaryId: u.primary_position_id })}
                  className={`${SMALL_BTN} hover:border-sky-500 hover:text-sky-400`}
                >
                  Edit access
                </button>
              </div>
            )
          )}
        </div>
      </div>
    </div>
  );
}
