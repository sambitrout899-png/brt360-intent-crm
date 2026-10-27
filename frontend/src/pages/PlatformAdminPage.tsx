import ModuleSelector, { LEGACY_MODULES } from "../components/ModuleSelector";
import { useEffect, useState } from "react";
import { createTenant, listTenants, logout, updateTenant } from "../api/client";
import { TENANT_VERTICALS } from "../api/types";
import type { Tenant } from "../api/types";
import { BrtLogo } from "../components/shell/BrtBrand";

const MAX_LOGO_KB = 512;
const FIELD = "mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200";

function TenantLogo({ tenant, size = "h-9 w-9" }: { tenant: Pick<Tenant, "name" | "display_name" | "logo_data_url" | "brand_color">; size?: string }) {
  const name = tenant.display_name || tenant.name;
  if (tenant.logo_data_url) {
    return <img src={tenant.logo_data_url} alt={`${name} logo`} className={`${size} rounded-md bg-white/5 object-contain`} />;
  }
  return (
    <span className={`${size} flex items-center justify-center rounded-md text-sm font-bold text-white`} style={{ background: tenant.brand_color || "#334155" }}>
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

/** Edit a tenant's details and branding - what its users see in the header. */
function TenantEditor({ tenant, onSaved, onCancel }: { tenant: Tenant; onSaved: () => void; onCancel: () => void }) {
  const [form, setForm] = useState({
    licensed_modules: tenant.licensed_modules ?? LEGACY_MODULES,
    name: tenant.name,
    display_name: tenant.display_name ?? "",
    vertical: tenant.vertical,
    brand_color: tenant.brand_color ?? "",
    logo_data_url: tenant.logo_data_url ?? "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function pickLogo(file: File | undefined) {
    if (!file) return;
    if (!/^image\/(png|jpeg|gif|webp|svg\+xml)$/.test(file.type)) {
      setError("Pick a PNG, JPEG, GIF, WebP or SVG image.");
      return;
    }
    if (file.size > MAX_LOGO_KB * 1024) {
      setError(`That image is ${Math.round(file.size / 1024)} KB - the limit is ${MAX_LOGO_KB} KB.`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      setError("");
      setForm((f) => ({ ...f, logo_data_url: String(reader.result) }));
    };
    reader.readAsDataURL(file);
  }

  async function save() {
    if (!form.name.trim()) {
      setError("The tenant needs a name.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await updateTenant(tenant.id, {
        licensed_modules: form.licensed_modules,
        name: form.name.trim(),
        display_name: form.display_name.trim(),
        vertical: form.vertical,
        brand_color: form.brand_color,
        logo_data_url: form.logo_data_url,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setBusy(false);
    }
  }

  const preview = { name: form.name || tenant.name, display_name: form.display_name, logo_data_url: form.logo_data_url || null, brand_color: form.brand_color || null };

  return (
    <div className="space-y-4 border-t border-slate-800 px-4 py-4">
      <ModuleSelector value={form.licensed_modules} onChange={licensed_modules => setForm({ ...form, licensed_modules })} />
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-slate-400">
          Name
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={FIELD} />
        </label>
        <label className="text-xs text-slate-400">
          Company name shown to users
          <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder={form.name} className={FIELD} />
        </label>
        <label className="text-xs text-slate-400">
          Vertical
          <select value={form.vertical} onChange={(e) => setForm({ ...form, vertical: e.target.value })} className={FIELD}>
            {TENANT_VERTICALS.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-400">
          Slug (permanent)
          <input value={tenant.slug} disabled className={`${FIELD} opacity-60`} />
        </label>
        <div className="text-xs text-slate-400">
          Logo
          <div className="mt-1 flex items-center gap-3">
            <TenantLogo tenant={preview} size="h-12 w-12" />
            <label className="cursor-pointer rounded-md border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-sky-500">
              {form.logo_data_url ? "Replace…" : "Upload…"}
              <input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" className="hidden" onChange={(e) => pickLogo(e.target.files?.[0])} />
            </label>
            {form.logo_data_url && (
              <button onClick={() => setForm({ ...form, logo_data_url: "" })} className="text-xs text-slate-500 hover:text-red-400">
                Remove
              </button>
            )}
          </div>
          <p className="mt-1 text-[11px] text-slate-500">PNG, JPEG, GIF, WebP or SVG, up to {MAX_LOGO_KB} KB. A square or wide logo works best.</p>
        </div>
        <label className="text-xs text-slate-400">
          Accent colour
          <div className="mt-1 flex items-center gap-2">
            <input type="color" value={form.brand_color || "#0ea5e9"} onChange={(e) => setForm({ ...form, brand_color: e.target.value })} className="h-9 w-12 cursor-pointer rounded border border-slate-700 bg-slate-950" />
            <span className="font-mono text-xs text-slate-400">{form.brand_color || "default"}</span>
            {form.brand_color && (
              <button onClick={() => setForm({ ...form, brand_color: "" })} className="text-xs text-slate-500 hover:text-red-400">
                Reset
              </button>
            )}
          </div>
        </label>
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-950 px-4 py-3">
        <p className="mb-2 text-[10px] font-bold uppercase tracking-wide text-slate-500">How users will see it</p>
        <div className="flex items-center gap-3">
          <TenantLogo tenant={preview} />
          <span className="font-bold text-slate-100">{preview.display_name || preview.name}</span>
          <span className="h-4 w-px bg-slate-700" />
          <span className="text-xs text-slate-500">
            Intent CRM <span style={{ color: preview.brand_color || "#0ea5e9" }}>·</span> BRT 360
          </span>
        </div>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}
      <div className="flex gap-2">
        <button onClick={save} disabled={busy} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500 disabled:opacity-50">
          {busy ? "Saving…" : "Save"}
        </button>
        <button onClick={onCancel} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300">
          Cancel
        </button>
      </div>
    </div>
  );
}

function emptyForm() {
  return { licensed_modules: ["telecom_crm"], name: "", slug: "", vertical: TENANT_VERTICALS[2] as string, adminEmail: "", adminPassword: "", adminName: "Admin" };
}

export default function PlatformAdminPage({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [form, setForm] = useState(emptyForm());
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [saved, setSaved] = useState("");

  function load() {
    listTenants().then(setTenants);
  }

  useEffect(load, []);

  async function submit() {
    setError(null);
    const slug = form.slug.trim().toLowerCase();
    if (!form.name.trim() || !form.slug.trim() || !form.adminEmail.trim() || form.adminPassword.length < 8) {
      setError("Name, slug, admin email, and an admin password (8+ characters) are all required.");
      return;
    }
    if (!/^[a-z0-9][a-z0-9-]*$/.test(slug) || slug.length > 100) {
      setError("Slug must start with a letter or number and contain only letters, numbers and hyphens (up to 100 characters).");
      return;
    }
    try {
      await createTenant({
        licensed_modules: form.licensed_modules,
        name: form.name.trim(),
        slug,
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
        <BrtLogo className="h-8" />
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-100">Platform Admin</h1>
            <p className="mt-1 text-sm text-slate-400">
              Create, edit and brand tenants. Each is fully data-isolated, seeded immediately with its own reference data
              and first admin account; its logo and company name appear for all of its users.
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

        {saved && <p className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-300">{saved}</p>}
        <div className="space-y-2">
          {tenants.map((t) => (
            <div key={t.id} className={`rounded-lg border bg-slate-900/60 ${editing === t.id ? "border-sky-500/40" : "border-slate-700"}`}>
              <div className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
                <TenantLogo tenant={t} />
                <span className="font-semibold text-slate-200">{t.display_name || t.name}</span>
                {t.display_name && t.display_name !== t.name && <span className="text-xs text-slate-500">({t.name})</span>}
                <span className="font-mono text-xs text-slate-500">{t.slug}</span>
                <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{t.vertical}</span>
                <span className="text-xs text-sky-300">{(t.licensed_modules ?? LEGACY_MODULES).join(" · ") || "No active modules"}</span>
                {editing !== t.id && (
                  <button
                    onClick={() => {
                      setSaved("");
                      setEditing(t.id);
                    }}
                    className="ml-auto rounded-md border border-slate-700 px-3 py-1 text-xs font-semibold text-slate-300 hover:border-sky-500 hover:text-sky-300"
                  >
                    Edit
                  </button>
                )}
              </div>
              {editing === t.id && (
                <TenantEditor
                  tenant={t}
                  onCancel={() => setEditing(null)}
                  onSaved={() => {
                    setEditing(null);
                    setSaved(`Saved ${t.slug} - its users see the new branding the next time a page loads.`);
                    load();
                  }}
                />
              )}
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
              <input value={form.slug} onChange={(e) => setForm((f) => ({ ...f, slug: e.target.value.toLowerCase() }))} maxLength={100} className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-200" placeholder="acme-telecom" />
              <span className="mt-1 block text-xs text-slate-500">Letters are saved as lowercase. Use letters, numbers and hyphens, without spaces.</span>
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
          <ModuleSelector value={form.licensed_modules} onChange={licensed_modules => setForm({ ...form, licensed_modules })} />
        {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
          <button onClick={submit} className="mt-4 rounded-lg bg-sky-600 px-4 py-2 text-sm font-bold text-white hover:bg-sky-500">
            Create Tenant
          </button>
        </div>
      </div>
    </div>
  );
}
