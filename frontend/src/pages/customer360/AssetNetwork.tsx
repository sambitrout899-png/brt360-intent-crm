import type { Asset } from "../../api/types";
import type { CustomerNetwork } from "../../api/c360Types";
import { servicesForAsset } from "./networkOfAsset";
import { useWorkspace } from "../../workspace";
import { StatusChip } from "./charts";

/** The asset's network: where it is served from, the path it runs on, every resource it holds - live from
 *  Network Inventory - and what puts it at risk, with the impact and fault-report intents one click away. */
export default function AssetNetwork({ network, asset, error }: { network: CustomerNetwork | null; asset: Asset; error: string | null }) {
  const { runIntent, canRun } = useWorkspace();
  if (error) return <p className="asset-empty">{error}</p>;
  if (!network) return <p className="asset-empty">Loading the network…</p>;
  if (!network.in_use) return <p className="asset-empty">Network Inventory isn't recording this organisation's network yet - the asset's resources come from the integrated inventory system (Inventory reference on Overview).</p>;
  const services = servicesForAsset(network, asset);
  if (!services.length) return <p className="asset-empty">No service record in Network Inventory is linked to this asset yet - it is created when the order's service is designed in fulfillment.</p>;
  return <>{services.map((s) => {
    const firstTrouble = s.trouble[0]?.split(" is ")[0];
    return <div key={s.id} className="asset-network">
      <h5>{s.service} <StatusChip value={s.oper_status} /> <StatusChip value={s.status} /></h5>
      {s.trouble.length > 0 && <p className="asset-empty" role="alert">⚠ {s.trouble.join("; ")}</p>}
      <dl className="asset-facts">
        <div><dt>Product</dt><dd>{s.product || "—"}</dd></div>
        <div><dt>Served from</dt><dd>{s.location ?? "—"}</dd></div>
        <div><dt>Network path</dt><dd>{s.path || "—"}</dd></div>
        <div><dt>Order</dt><dd>{s.order_id ? `#${s.order_id}` : "—"}</dd></div>
        <div><dt>Service record</dt><dd>{s.brt_id}</dd></div>
        <div><dt>Address</dt><dd>{s.address || "—"}</dd></div>
      </dl>
      <div className="asset-table-scroll"><table className="asset-table"><thead><tr><th>Resource</th><th>Identifier</th><th>Status</th><th>Operational</th><th>Path</th></tr></thead>
        <tbody>{s.uses.map((r) => <tr key={r.id}>
          <td>{r.type_name}<small>{r.site ?? ""}</small></td><td>{r.identifier}{r.serial && <small>S/N {r.serial}</small>}</td>
          <td><StatusChip value={r.status} /></td><td><StatusChip value={r.oper_status} /></td><td>{r.path || r.location_path || "—"}</td>
        </tr>)}</tbody></table></div>
      <div className="asset-actions">
        {canRun("INT-126") && <button onClick={() => runIntent("INT-126", { item: firstTrouble ?? s.service })}>Analyse impact{firstTrouble ? ` of ${firstTrouble}` : ""}</button>}
        {canRun("INT-120") && <button onClick={() => runIntent("INT-120", { question: `inventory of order ${s.order_id ?? ""}`.trim() })}>Everything the order holds</button>}
        {canRun("INT-10") && s.trouble.length > 0 && <button onClick={() => runIntent("INT-10", { description: `Service ${s.service} affected: ${s.trouble.join("; ")}` })}>Open a proactive ticket</button>}
      </div>
    </div>;
  })}</>;
}
