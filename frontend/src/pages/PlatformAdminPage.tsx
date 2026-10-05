import { useEffect, useState } from "react";
import { createTenant, listTenants, logout } from "../api/client";
import { TENANT_VERTICALS } from "../api/types";
import type { Tenant } from "../api/types";

function emptyForm() {
  return { name: "", slug: "", vertical: TENANT_VERTICALS[2] as string, adminEmail: "", adminPassword: "", adminName: "Admin" };
}

export default function PlatformAdminPage({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [form, setForm] = useState(emptyForm());
  const [error, setError] = useState<string | null>(null);

  function load() {
    listTenants().then(setTenants);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    if (!form.name.trim() || !form.slug.trim() || !form.adminEmail.trim() || form.adminPassword.length < 8) {
      setError("Name, slug, admin email, and an admin password (8+ characters) are all required.");
      return;
    }
    try {
      await createTenant({
        name: form.name.trim(),
        slug: form.slug.trim(),
        vertical: form.vertical,
        admin_email: form.adminEmail.trim(),
        admin_password: form.adminPassword,
        admin_name: form.adminName.trim() || "Admin",
      });
      setForm(emptyForm());
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create tenant.");
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 px-6 py-10">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-100">Platform Admin</h1>
            <p className="mt-1 text-sm text-slate-400">
              Create and view tenants. Each is fully data-isolated, seeded immediately with its own reference data and
              first admin account.
            </p>
          </div>
          <button
            onClick={() => {
              logout();
              onLoggedOut();
            }}
            className="rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 hover:border-red-500 hover:text-red-400"
          >
            Log Out
          </button>
        </div>

        <div className="space-y-2">
          {tenants.map((t) => (
            <div key={t.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-4 py-3 text-sm">
              <span className="font-semibold text-slate-200">{t.name}</span>
              <span className="font-mono text-xs text-slate-500">{t.slug}</span>
              <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{t.vertical}</span>
            </div>
          ))}
          {tenants.length === 0 && <p className="text-sm text-slate-500">No tenants yet.</p>}
        </div>

        <div className="rounded-xl border border-slate-700 bg-slate-900/60 p-5">
          <h2 className="mb-4 text-lg font-bold text-slate-100">New Tenant</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-xs text-slate-400">
              Name
              <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="Acme Telecom" />
            </label>
            <label className="text-xs text-slate-400">
              Slug
              <input value={form.slug} onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value }))} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="acme-telecom" />
            </label>
            <label className="text-xs text-slate-400">
              Vertical
              <select value={form.vertical} onChange={(e) => setForm((f) => ({ ...f, vertical: e.target.value }))} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200">
                {TENANT_VERTICALS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
            <div />
            <label className="text-xs text-slate-400">
              First admin's email
              <input type="email" value={form.adminEmail} onChange={(e) => setForm((f) => ({ ...f, adminEmail: e.target.value }))} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="admin@acme-telecom.com" />
            </label>
            <label className="text-xs text-slate-400">
              First admin's password
              <input type="password" value={form.adminPassword} onChange={(e) => setForm((f) => ({ ...f, adminPassword: e.target.value }))} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="8+ characters" />
            </label>
            <label className="text-xs text-slate-400">
              First admin's name
              <input value={form.adminName} onChange={(e) => setForm((f) => ({ ...f, adminName: e.target.value }))} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" />
            </label>
          </div>
          {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
          <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
            Create Tenant
          </button>
        </div>
      </div>
    </div>
  );
}
