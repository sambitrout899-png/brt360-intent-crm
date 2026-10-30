import { useEffect, useState } from "react";
import { functionMapVersions } from "../api/client";
import {
  changeMe,
  createRelease,
  listReleases,
  listWorkspaces,
  releaseAction,
  simulateRelease,
  workspaceAction,
  type ChangeRelease,
  type ChangeWorkspace,
  type ReleaseSimulation,
} from "../api/changeControl";
import type { MapVersion } from "../api/functionMapTypes";

const STATUS: Record<string, string> = {
  open: "border-sky-500/40 text-sky-200",
  submitted: "border-amber-500/40 text-amber-200",
  released: "border-emerald-500/40 text-emerald-200",
  discarded: "border-slate-600 text-slate-500",
  draft: "border-amber-500/40 text-amber-200",
  deployed: "border-emerald-500/40 text-emerald-200",
  rolled_back: "border-slate-600 text-slate-400",
};

function Chip({ status }: { status: string }) {
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${STATUS[status] ?? "border-slate-700 text-slate-400"}`}>{status.replace("_", " ")}</span>;
}

const when = (iso: string | null) => (iso ? iso.replace("T", " ").slice(0, 16) : "");

/** Admin -> Releases: every administrator's workspaces, and the releases the
 * organization's root administrator prepares, simulates, deploys (all or
 * nothing) and rolls back. */
export default function AdminReleasesSection() {
  const [me, setMe] = useState<{ user_id: number; is_root: boolean; root_admin: { id: number; name: string } | null } | null>(null);
  const [workspaces, setWorkspaces] = useState<ChangeWorkspace[]>([]);
  const [releases, setReleases] = useState<ChangeRelease[]>([]);
  const [drafts, setDrafts] = useState<MapVersion[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [mapVersion, setMapVersion] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [sims, setSims] = useState<Record<number, ReleaseSimulation>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function load() {
    changeMe().then(setMe).catch(() => setMe(null));
    listWorkspaces().then(setWorkspaces).catch((e) => setError(String(e.message ?? e)));
    listReleases().then(setReleases).catch(() => setReleases([]));
    functionMapVersions().then((v) => setDrafts(v.versions.filter((x) => x.status === "draft"))).catch(() => setDrafts([]));
  }
  useEffect(load, []);

  async function act(job: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await job();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work");
    } finally {
      setBusy(false);
    }
  }

  const submitted = workspaces.filter((w) => w.status === "submitted");
  const btn = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-semibold text-slate-200 hover:border-slate-500 disabled:opacity-40";
  const panel = "rounded-xl border border-slate-800 bg-slate-900/50 p-4";

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">🚀 Releases & workspaces</h2>
        <p className="text-xs text-slate-400">
          Administrators develop in workspaces - what they change is a draft there, locked to them, so nobody overwrites anyone. A submitted workspace goes
          live only in a release, which{" "}
          <span className="font-semibold text-slate-200">{me?.root_admin?.name ?? "the root administrator"}</span> (the organization's root administrator)
          simulates, deploys and, if need be, rolls back.
          {me && !me.is_root && " You can see everything here; preparing and deploying releases is theirs."}
        </p>
      </div>
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}

      {me?.is_root && (
        <section className={panel}>
          <h3 className="text-sm font-semibold text-slate-100">New release</h3>
          {submitted.length === 0 && drafts.length === 0 ? (
            <p className="mt-1 text-xs text-slate-500">Nothing is waiting - administrators submit their workspaces from the bar above Flow Studio.</p>
          ) : (
            <div className="mt-2 space-y-2 text-xs">
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Release name (e.g. November sales changes)"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100" />
              {submitted.map((w) => (
                <label key={w.id} className="flex items-start gap-2 rounded-md border border-slate-800 px-2.5 py-1.5 text-slate-300">
                  <input type="checkbox" checked={picked.includes(w.id)} onChange={(e) => setPicked((p) => (e.target.checked ? [...p, w.id] : p.filter((x) => x !== w.id)))} />
                  <span>
                    <span className="font-semibold text-slate-100">{w.name}</span> by {w.owner} -{" "}
                    {w.objects.map((o) => `${o.kind} “${o.label}” (${o.change})`).join(", ")}
                  </span>
                </label>
              ))}
              {drafts.length > 0 && (
                <label className="flex items-center gap-2 text-slate-300">
                  and activate Functional Map version
                  <select value={mapVersion ?? ""} onChange={(e) => setMapVersion(e.target.value ? Number(e.target.value) : null)}
                    className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-100">
                    <option value="">- none -</option>
                    {drafts.map((v) => (
                      <option key={v.id} value={v.id}>{v.label ?? `v${v.number}`} · {v.name}</option>
                    ))}
                  </select>
                </label>
              )}
              <button disabled={busy || (!picked.length && !mapVersion)} className={`${btn} border-sky-500/50 text-sky-200`}
                onClick={() => act(async () => { await createRelease(name, picked, mapVersion); setPicked([]); setMapVersion(null); setName(""); })}>
                Prepare the release
              </button>
            </div>
          )}
        </section>
      )}

      <section className={panel}>
        <h3 className="text-sm font-semibold text-slate-100">Releases</h3>
        {releases.length === 0 && <p className="mt-1 text-xs text-slate-500">None yet.</p>}
        <ul className="mt-2 space-y-2">
          {releases.map((r) => {
            const sim = sims[r.id];
            return (
              <li key={r.id} className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2 text-xs text-slate-300">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip status={r.status} />
                  <span className="font-semibold text-slate-100">Release {r.number} · {r.name}</span>
                  <span className="text-slate-500">
                    {r.workspaces.map((w) => w.name).join(", ")}
                    {r.map_version_id ? " + a map version" : ""}
                    {r.deployed_at ? ` · deployed ${when(r.deployed_at)} by ${r.deployed_by}` : ""}
                    {r.rolled_back_at ? ` · rolled back ${when(r.rolled_back_at)}` : ""}
                  </span>
                  <span className="flex-1" />
                  {me?.is_root && r.status === "draft" && (
                    <>
                      <button disabled={busy} className={btn} onClick={() => act(async () => { const s = await simulateRelease(r.id); setSims((x) => ({ ...x, [r.id]: s })); })}>
                        ▶ Simulate
                      </button>
                      <button disabled={busy} className={`${btn} border-emerald-500/50 text-emerald-200`}
                        onClick={() => window.confirm(`Deploy release ${r.number}? Every change in it goes live together.`) && act(() => releaseAction(r.id, "deploy"))}>
                        Deploy
                      </button>
                    </>
                  )}
                  {me?.is_root && r.status === "deployed" && (
                    <button disabled={busy} className={`${btn} hover:border-amber-500/60`}
                      onClick={() => window.confirm(`Roll back release ${r.number}? What it changed goes back to how it was.`) && act(() => releaseAction(r.id, "rollback"))}>
                      ↺ Roll back
                    </button>
                  )}
                </div>
                {sim && (
                  <div className={`mt-2 rounded-md border px-2.5 py-1.5 ${sim.ok ? "border-emerald-500/30 text-emerald-200" : "border-amber-500/30 text-amber-200"}`}>
                    {sim.ok ? "✓ The simulation passed - nothing was kept." : "The simulation found problems - nothing was kept."} {sim.impact?.headline ?? ""}
                    <ul className="mt-1 list-disc pl-5 text-slate-300">
                      {[...sim.checks, ...sim.problems.filter((p) => p.object === "Functional Map")].map((c, i) => (
                        <li key={i} className={c.ok ? "" : "text-amber-200"}>{c.object}: {c.message}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section className={panel}>
        <h3 className="text-sm font-semibold text-slate-100">Workspaces</h3>
        {workspaces.length === 0 && <p className="mt-1 text-xs text-slate-500">None yet - start one from the bar above Flow Studio.</p>}
        <ul className="mt-2 space-y-1.5">
          {workspaces.map((w) => (
            <li key={w.id} className="flex flex-wrap items-baseline gap-2 text-xs text-slate-300">
              <Chip status={w.status} />
              <span className="font-semibold text-slate-100">{w.name}</span>
              <span className="text-slate-500">
                {w.owner} · {w.objects.length} change{w.objects.length === 1 ? "" : "s"}
                {w.objects.length ? `: ${w.objects.map((o) => `${o.kind} “${o.label}”`).join(", ")}` : ""}
              </span>
              {w.status === "submitted" && w.owner_id === me?.user_id && (
                <button disabled={busy} className="text-sky-400 hover:text-sky-300" onClick={() => act(() => workspaceAction(w.id, "reopen"))}>
                  reopen
                </button>
              )}
              {(w.status === "open" || w.status === "submitted") && (w.owner_id === me?.user_id || me?.is_root) && (
                <button disabled={busy} className="text-red-400 hover:text-red-300"
                  onClick={() => window.confirm(`Discard “${w.name}”? Its drafts are dropped and its locks freed.`) && act(() => workspaceAction(w.id, "discard"))}>
                  discard
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
