import { useEffect, useState } from "react";
import { getWorkspace, setWorkspace } from "../../api/client";
import { changeMe, createWorkspace, listWorkspaces, workspaceAction, type ChangeWorkspace } from "../../api/changeControl";

/** Where an administrator's changes go: live, or one of their workspaces
 * (change control). In a workspace every save is a draft there, and what
 * they change is locked to them until the root administrator releases it. */
export default function WorkspaceBar({ onOpenReleases }: { onOpenReleases?: () => void }) {
  const [current, setCurrent] = useState<number | null>(getWorkspace());
  const [mine, setMine] = useState<ChangeWorkspace[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function refresh() {
    Promise.all([changeMe(), listWorkspaces()])
      .then(([me, all]) => {
        const open = all.filter((w) => w.owner_id === me.user_id && w.status === "open");
        setMine(open);
        if (current && !open.some((w) => w.id === current)) choose(null); // submitted, released or discarded meanwhile
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Couldn't load your workspaces"));
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(refresh, []);

  function choose(id: number | null) {
    setCurrent(id);
    setWorkspace(id);
  }

  async function act(job: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await job();
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work");
    } finally {
      setBusy(false);
    }
  }

  const ws = mine.find((w) => w.id === current);
  const btn = "rounded-md border border-slate-700 px-2.5 py-1 text-xs font-semibold text-slate-200 hover:border-slate-500 disabled:opacity-40";
  return (
    <div className={`rounded-xl border px-3 py-2 text-xs ${ws ? "border-violet-500/40 bg-violet-500/[0.07]" : "border-slate-800 bg-slate-900/50"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-slate-300">{ws ? "🧪 Working in" : "⚡ Changes go"}</span>
        <select
          value={current ?? ""}
          onChange={(e) => choose(e.target.value ? Number(e.target.value) : null)}
          className="rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs font-semibold text-slate-100"
        >
          <option value="">straight live (no workspace)</option>
          {mine.map((w) => (
            <option key={w.id} value={w.id}>
              workspace “{w.name}”
            </option>
          ))}
        </select>
        <button
          disabled={busy}
          className={btn}
          onClick={() => {
            const name = window.prompt("Name the workspace (what you are working on)", "")?.trim();
            if (name) void act(async () => choose((await createWorkspace(name)).id));
          }}
        >
          ＋ New workspace
        </button>
        {ws && (
          <>
            <span className="text-slate-400">
              {ws.objects.length} change{ws.objects.length === 1 ? "" : "s"} · locked to you · nothing live changes until a release
            </span>
            <span className="flex-1" />
            <button
              disabled={busy || !ws.objects.length}
              className={`${btn} border-emerald-500/50 text-emerald-200`}
              onClick={() => act(async () => { await workspaceAction(ws.id, "submit"); choose(null); })}
              title="Ready for the root administrator to put into a release"
            >
              Submit for release
            </button>
            <button
              disabled={busy}
              className={`${btn} hover:border-red-500/60 hover:text-red-300`}
              onClick={() => window.confirm(`Discard “${ws.name}”? Its drafts are dropped and its locks freed.`) && act(async () => { await workspaceAction(ws.id, "discard"); choose(null); })}
            >
              Discard
            </button>
          </>
        )}
        {onOpenReleases && (
          <button className="ml-auto text-sky-400 hover:text-sky-300" onClick={onOpenReleases}>
            Releases →
          </button>
        )}
      </div>
      {ws && ws.objects.length > 0 && (
        <p className="mt-1 text-slate-400">
          {ws.objects.map((o) => `${o.kind} “${o.label}” (${o.change}, rev ${o.revision})`).join(" · ")}
        </p>
      )}
      {error && <p className="mt-1 text-red-300">{error}</p>}
    </div>
  );
}
