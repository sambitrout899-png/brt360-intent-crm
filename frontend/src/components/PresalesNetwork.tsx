import { useCallback, useEffect, useState } from "react";
import { invApi } from "../api/client";
import { useIntentRefresh, useWorkspace } from "../workspace";

type Check = { resource_type: string; kept: boolean; available: boolean | null; free: number | null; candidate: string | null; detail: string };
type Hold = { id: number; identifier: string; type_code: string; type_name: string; lifecycle: string; location: string | null; order_id: number | null };
type View = {
  in_use: boolean; opportunity_id: number; opportunity?: string; stage: string; product?: string | null; address: string;
  location?: string | null; location_name?: string | null; resources?: string[]; resources_from?: string; checks: Check[]; holds: Hold[]; feasible?: boolean;
};

const LABEL: Record<string, string> = { ip_block: "IPv4 block", ipv6_prefix: "IPv6 prefix", vlan: "VLAN", ont: "ONT", cpe: "CPE", sim: "SIM", port: "Port", splitter_port: "Splitter port", fibre_strand: "Fibre strand", router: "Router", phone_number: "Phone number", sip_trunk: "SIP trunk", circuit_id: "Circuit id" };
const label = (t: string) => LABEL[t] ?? t.replace(/_/g, " ");
const BTN = "rounded-md border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-300 transition hover:border-sky-500 hover:text-sky-300 disabled:opacity-40";
const PRIMARY = "rounded-md bg-sky-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-500 disabled:opacity-40";

function errorText(e: unknown, fallback: string) {
  if (!(e instanceof Error)) return fallback;
  const m = e.message.match(/^\d{3} [^:]*: (.*)$/s);
  if (m) {
    try {
      const detail = JSON.parse(m[1]).detail;
      if (typeof detail === "string") return detail;
    } catch {
      /* not JSON */
    }
  }
  return e.message || fallback;
}

/** Presales' network check for a deal, integrated in the pipeline: the site, the location serving it, each
 * resource the product needs and whether it's free there - from BRT 360 Network Inventory - what is held for the
 * deal, and the decision. Reserve holds the items (they move to the order the deal becomes); Feasible passes
 * feasibility (INT-18) - which keeps the hold, or takes it if nothing was reserved yet. */
export default function PresalesNetwork({ opportunityId, onChanged, decisions = true }: {
  opportunityId: number;
  onChanged?: () => void;
  /** show Feasible / Not feasible here (false where the page already offers them, e.g. the record panel's actions) */
  decisions?: boolean;
}) {
  const { runIntent, canRun } = useWorkspace();
  const [view, setView] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    invApi<View>(`/opportunities/${opportunityId}`).then((v) => { setView(v); setError(null); })
      .catch((e) => setError(errorText(e, "Could not check the network")));
  }, [opportunityId]);
  useEffect(load, [load]);
  useIntentRefresh(load);  // Feasible / Not feasible run as intents - show what they did

  async function act(path: "hold" | "release") {
    setBusy(true);
    setError(null);
    try {
      setView(await invApi<View>(`/opportunities/${opportunityId}/${path}`, "POST", path === "release" ? { note: "released by Presales" } : {}));
      onChanged?.();
    } catch (e) {
      setError(errorText(e, path === "hold" ? "Could not reserve" : "Could not release"));
    } finally {
      setBusy(false);
    }
  }

  if (!view) return <p className="text-sm text-slate-400">{error ?? "Checking the network…"}</p>;
  if (!view.in_use) {
    return <p className="text-sm text-slate-400">No network is recorded in BRT 360 Network Inventory yet - feasibility runs on the configured checks only.</p>;
  }
  const decided = view.stage !== "in_feasibility";
  return (
    <div className="space-y-4 text-sm">
      <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Site</p>
        <p className="text-slate-200">{view.address || <span className="text-amber-300">No site address - the Account Manager sends it with the deal</span>}</p>
        <p className="mt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Served from</p>
        <p className={view.location ? "text-slate-200" : "text-red-300"}>{view.location ? `${view.location} · ${view.location_name}` : "No location in Network Inventory serves this site"}</p>
      </div>

      <div>
        <p className="mb-1 text-xs text-slate-500">{view.product ?? "The product"} needs - from {view.resources_from}:</p>
        <ul className="divide-y divide-slate-800 rounded-xl border border-slate-800">
          {view.checks.map((c) => {
            const hold = view.holds.find((h) => h.type_code === c.resource_type);
            const ok = hold || c.available !== false;
            return (
              <li key={c.resource_type} className="flex items-center gap-3 px-3 py-2.5">
                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${hold ? "bg-sky-500/15 text-sky-300" : ok ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}>
                  {hold ? "●" : ok ? "✓" : "!"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold text-slate-200">{label(c.resource_type)}</span>
                  <span className="block truncate text-xs text-slate-500">{hold ? `Held for this deal: ${hold.identifier}${hold.location ? ` at ${hold.location}` : ""}` : c.detail}</span>
                </span>
                {!hold && c.candidate && <span className="shrink-0 font-mono text-[11px] text-slate-400" title="What a reservation would take">{c.candidate}</span>}
              </li>
            );
          })}
        </ul>
      </div>

      {error && <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}

      <div className="flex flex-wrap gap-2">
        {view.holds.length === 0 ? (
          <button type="button" disabled={busy || !view.feasible} onClick={() => act("hold")} className={PRIMARY} title={view.feasible ? "" : "Something the deal needs isn't available at the site"}>
            Reserve these
          </button>
        ) : (
          <button type="button" disabled={busy || view.holds.some((h) => h.order_id)} onClick={() => act("release")} className={BTN}>Release the hold</button>
        )}
        {decisions && !decided && canRun("INT-18") && (
          <>
            <button type="button" disabled={!view.feasible && view.holds.length === 0} onClick={() => runIntent("INT-18", { opportunity_id: opportunityId, decision: "feasible" })}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-40">
              Feasible - pass
            </button>
            <button type="button" onClick={() => runIntent("INT-18", { opportunity_id: opportunityId, decision: "not_feasible" })} className={BTN}>Not feasible</button>
          </>
        )}
        <button type="button" onClick={load} className={`${BTN} ml-auto`}>↻ Recheck</button>
      </div>
      <p className="text-[11px] text-slate-500">
        A hold lasts {30} days and is renewed while the deal is open; it moves to the order when the PO is matched, and Fulfillment uses exactly these items.
        Not feasible - or a lost deal - gives them back.
      </p>
    </div>
  );
}
