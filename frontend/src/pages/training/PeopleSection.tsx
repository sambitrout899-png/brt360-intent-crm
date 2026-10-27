import { useState } from "react";
import { request } from "../../api/client";
import { tr } from "./api";
import type { DirectoryUser, ImportReport, Location } from "./api";
import { useAction, useLoad } from "./hooks";
import { Badge, Card, Note, Table, btnDanger, btnGhost, btnPrimary, field, label } from "./ui";

/* People and places: users' training roles and scopes (ORG-005/006),
   the participant directory with CSV import and erasure (ORG-004/011),
   and locations (ORG-002). */

const ROLES = ["hr", "facilitator", "executive", "participant"];

function AccessEditor({ u, locations, departments, onSaved }: { u: DirectoryUser; locations: Location[]; departments: { id: number; name: string }[]; onSaved: () => void }) {
  const [roles, setRoles] = useState<string[]>(u.roles.filter((r) => r !== "admin"));
  const [locs, setLocs] = useState<number[]>(u.scope_location_ids);
  const [deps, setDeps] = useState<number[]>(u.scope_department_ids);
  const [phone, setPhone] = useState(u.phone);
  const [profile, setProfile] = useState({
    bio: String(u.profile.bio ?? ""),
    expertise: String(u.profile.expertise ?? ""),
    languages: String(u.profile.languages ?? ""),
    vendor: String(u.profile.vendor ?? ""),
    is_external: !!u.profile.is_external,
  });
  const action = useAction();
  const scoped = roles.includes("hr") || roles.includes("executive");
  return (
    <div className="space-y-3 rounded-xl border border-sky-500/30 bg-slate-950/40 p-3">
      <div className="flex flex-wrap gap-3 text-sm">
        {ROLES.map((r) => (
          <label key={r} className="flex items-center gap-1.5 capitalize">
            <input type="checkbox" checked={roles.includes(r)} onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />
            {r}
          </label>
        ))}
      </div>
      {scoped && (
        <div className="grid gap-2 md:grid-cols-2">
          <label className={label}>
            Limit to locations (none = whole org)
            <select multiple className={`${field} h-20`} value={locs.map(String)} onChange={(e) => setLocs(Array.from(e.target.selectedOptions).map((o) => Number(o.value)))}>
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <label className={label}>
            Limit to departments (and their sub-departments)
            <select multiple className={`${field} h-20`} value={deps.map(String)} onChange={(e) => setDeps(Array.from(e.target.selectedOptions).map((o) => Number(o.value)))}>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <label className={label}>
        Phone (used for QR identification by its last 4 digits)
        <input className={field} value={phone} onChange={(e) => setPhone(e.target.value)} />
      </label>
      {roles.includes("facilitator") && (
        <div className="grid gap-2 md:grid-cols-2">
          <label className={label}>
            Bio
            <textarea className={field} value={profile.bio} onChange={(e) => setProfile({ ...profile, bio: e.target.value })} />
          </label>
          <div className="space-y-2">
            <input className={field} placeholder="Expertise areas" value={profile.expertise} onChange={(e) => setProfile({ ...profile, expertise: e.target.value })} />
            <input className={field} placeholder="Languages" value={profile.languages} onChange={(e) => setProfile({ ...profile, languages: e.target.value })} />
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={profile.is_external} onChange={(e) => setProfile({ ...profile, is_external: e.target.checked })} /> External
              </label>
              <input className={field} placeholder="Vendor company" value={profile.vendor} onChange={(e) => setProfile({ ...profile, vendor: e.target.value })} />
            </div>
          </div>
        </div>
      )}
      <Note>{action.error}</Note>
      <button
        type="button"
        className={btnPrimary}
        disabled={action.busy}
        onClick={() =>
          action
            .run(() => tr.setAccess(u.id, { roles, scope_location_ids: scoped ? locs : [], scope_department_ids: scoped ? deps : [], phone, profile }), "Saved.")
            .then((o) => o !== undefined && onSaved())
        }
      >
        Save access
      </button>
    </div>
  );
}

function Locations({ canEdit }: { canEdit: boolean }) {
  const { data, error, reload } = useLoad(() => tr.locations(), []);
  const [f, setF] = useState({ name: "", code: "", address: "", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" });
  const action = useAction();
  return (
    <Card title="Locations">
      <Note>{error || action.error}</Note>
      <Table
        head={["Name", "Code", "Time zone", "Status", ""]}
        rows={(data ?? []).map((l) => [
          l.name,
          <span key="c" className="font-mono text-xs">
            {l.code}
          </span>,
          l.timezone,
          <Badge key="s" value={l.status === "active" ? "published" : "retired"}>
            {l.status}
          </Badge>,
          canEdit ? (
            <button key="t" type="button" className="text-xs text-sky-300" onClick={() => action.run(() => tr.editLocation(l.id, { status: l.status === "active" ? "inactive" : "active" })).then(reload)}>
              {l.status === "active" ? "Deactivate" : "Activate"}
            </button>
          ) : (
            ""
          ),
        ])}
        empty="No locations yet."
      />
      {canEdit && (
        <form
          className="mt-3 grid gap-2 sm:grid-cols-[1fr_100px_1fr_160px_auto]"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await action.run(() => tr.addLocation(f))) {
              setF({ ...f, name: "", code: "", address: "" });
              reload();
            }
          }}
        >
          <input required className={field} placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <input required className={field} placeholder="Code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
          <input className={field} placeholder="Address" value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
          <input className={field} placeholder="Asia/Kolkata" value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} aria-label="Time zone" />
          <button className={btnPrimary}>Add</button>
        </form>
      )}
    </Card>
  );
}

export default function PeopleSection({ isAdmin }: { isAdmin: boolean }) {
  const { data, error, reload } = useLoad(() => tr.people(), []);
  const locations = useLoad(() => tr.locations(), []);
  const departments = useLoad(() => request<{ departments: { id: number; name: string }[] }>("/api/training/workspace").then((w) => w.departments), []);
  const [editing, setEditing] = useState<number | null>(null);
  const [csv, setCsv] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [person, setPerson] = useState({ name: "", email: "", phone: "", organization: "", employee_id: "" });
  const [query, setQuery] = useState("");
  const action = useAction();
  const q = query.toLowerCase();
  return (
    <div className="space-y-4">
      <Note>{error}</Note>
      <Card title="Users with a login" actions={<input className={`${field} w-48`} placeholder="Search…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search people" />}>
        <ul className="divide-y divide-slate-800">
          {(data?.users ?? [])
            .filter((u) => !q || `${u.name} ${u.email}`.toLowerCase().includes(q))
            .map((u) => (
              <li key={u.id} className="space-y-2 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 text-sm">
                    {u.name} <span className="text-xs text-slate-500">{u.email}</span>
                  </span>
                  {u.roles.map((r) => (
                    <Badge key={r} value="scheduled">
                      {r}
                    </Badge>
                  ))}
                  {(u.scope_department_ids.length > 0 || u.scope_location_ids.length > 0) && <span className="text-[11px] text-amber-300">scoped</span>}
                  {isAdmin && !u.roles.includes("admin") && (
                    <button type="button" className="text-xs font-semibold text-sky-300" onClick={() => setEditing(editing === u.id ? null : u.id)}>
                      {editing === u.id ? "Close" : "Roles & scope"}
                    </button>
                  )}
                </div>
                {editing === u.id && (
                  <AccessEditor
                    u={u}
                    locations={locations.data ?? []}
                    departments={departments.data ?? []}
                    onSaved={() => {
                      setEditing(null);
                      reload();
                    }}
                  />
                )}
              </li>
            ))}
        </ul>
        <p className="mt-2 text-[11px] text-slate-500">Create login accounts under Admin → Users; assign training roles here. A person may hold several roles - their permission is the union.</p>
      </Card>
      <Card title="Participants without a login">
        <Table
          head={["Name", "Email", "Organization", "Status", ""]}
          rows={(data?.participants ?? [])
            .filter((p) => !q || `${p.name} ${p.email}`.toLowerCase().includes(q))
            .map((p) => [
              p.name,
              <span key="e" className="text-xs">
                {p.email}
                {p.email_invalid && <span className="ml-1 text-red-300">bounced</span>}
                {p.unsubscribed && <span className="ml-1 text-slate-500">no reminders</span>}
              </span>,
              p.organization,
              <Badge key="s" value={p.status === "erased" ? "cancelled" : "published"}>
                {p.status}
              </Badge>,
              isAdmin && !p.erased_at ? (
                <button key="x" type="button" className={btnDanger} onClick={() => window.confirm(`Erase ${p.name}'s personal data? Results stay in aggregates.`) && action.run(() => tr.erasePerson(p.id)).then(reload)}>
                  Erase
                </button>
              ) : (
                ""
              ),
            ])}
          empty="No external participants yet."
        />
        <form
          className="mt-3 grid gap-2 sm:grid-cols-[1fr_1fr_120px_1fr_auto]"
          onSubmit={async (e) => {
            e.preventDefault();
            if (await action.run(() => tr.addPerson(person), "Added.")) {
              setPerson({ name: "", email: "", phone: "", organization: "", employee_id: "" });
              reload();
            }
          }}
        >
          <input required className={field} placeholder="Name" value={person.name} onChange={(e) => setPerson({ ...person, name: e.target.value })} />
          <input required type="email" className={field} placeholder="Email" value={person.email} onChange={(e) => setPerson({ ...person, email: e.target.value })} />
          <input className={field} placeholder="Phone" value={person.phone} onChange={(e) => setPerson({ ...person, phone: e.target.value })} />
          <input className={field} placeholder="Organization" value={person.organization} onChange={(e) => setPerson({ ...person, organization: e.target.value })} />
          <button className={btnPrimary}>Add</button>
        </form>
        <div className="mt-4 space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">CSV import - name, email, phone, employee_id, location (code), department, roles, manager</p>
          <textarea className={`${field} h-24 font-mono text-xs`} value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={"name,email,phone,location\nAsha Rao,asha@example.com,+91 98450 12345,BLR"} />
          <div className="flex gap-2">
            <button type="button" className={btnGhost} disabled={!csv.trim()} onClick={() => action.run(() => tr.importPeople(csv, false)).then((r) => r && setReport(r))}>
              Validate
            </button>
            <button type="button" className={btnPrimary} disabled={!csv.trim()} onClick={() => action.run(() => tr.importPeople(csv, true)).then((r) => { if (r) { setReport(r); reload(); } })}>
              Import valid rows
            </button>
          </div>
          {report && (
            <div className="space-y-1 text-sm">
              <p>
                {report.valid} valid · {report.invalid} with errors · {report.created} created
              </p>
              <ul className="space-y-0.5 text-xs">
                {report.rows
                  .filter((r) => !r.ok)
                  .map((r) => (
                    <li key={r.row} className="text-red-300">
                      Row {r.row} ({r.email || "no email"}): {r.errors?.join("; ")}
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </div>
        <Note>{action.error}</Note>
        <Note kind="ok">{action.ok}</Note>
      </Card>
      <Locations canEdit={isAdmin} />
    </div>
  );
}
