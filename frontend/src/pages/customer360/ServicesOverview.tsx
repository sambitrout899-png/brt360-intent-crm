import type { CustomerNetwork } from "../../api/c360Types";
import type { Party360 } from "../../api/types";
import { StatusChip } from "./charts";
import { formatValue } from "./format";
import { servicesForAsset } from "./networkOfAsset";

type Values = Record<string, unknown>;
function snapshot(json: string | null | undefined): Values {
  try {
    const v = JSON.parse(json || "{}");
    return v && typeof v === "object" ? (v as Values) : {};
  } catch {
    return {};
  }
}

/** The customer's services on one card: what they have, whether it is live, and - from Network
 *  Inventory - whether the network under it is healthy and where it is served from. One line each;
 *  everything else is a click away on the Services tab. */
export default function ServicesOverview({ detail, network, onOpen }: { detail: Party360; network: CustomerNetwork | null; onOpen: (assetId?: number) => void }) {
  const rows = detail.service_accounts.flatMap((account) => account.assets.map((asset) => ({ account, asset, data: snapshot(asset.instance_json) })));
  const atRisk = rows.filter(({ asset }) => servicesForAsset(network, asset).some((s) => s.at_risk === "yes")).length;
  return (
    <section className="account-applet c360-services" aria-label="Services">
      <header className="account-applet-heading">
        <h3>Services <span>{rows.length}</span>{atRisk > 0 && <em className="c360-services-risk">{atRisk} at risk</em>}</h3>
        {rows.length > 0 && <button type="button" onClick={() => onOpen()}>Manage →</button>}
      </header>
      {rows.length === 0 ? (
        <p className="account-empty">No services yet - they appear here once an order is fulfilled.</p>
      ) : (
        <ul className="c360-service-list">
          {rows.map(({ asset, data }) => {
            const service = servicesForAsset(network, asset)[0];
            const price = data.net_unit_price != null && data.quantity != null ? Number(data.net_unit_price) * Number(data.quantity) : null;
            const path = service?.path ? service.path.split(" › ").slice(-2).join(" › ") : "";
            return (
              <li key={asset.id}>
                <button type="button" onClick={() => onOpen(asset.id)} data-risk={service?.at_risk === "yes" || undefined}>
                  <span className="c360-service-main">
                    <strong>{asset.asset_type}</strong>
                    <small>{asset.identifier || `Asset #${asset.id}`}</small>
                  </span>
                  <span className="c360-service-network">
                    {service ? (
                      <>
                        <StatusChip value={service.oper_status} />
                        <small title={service.trouble.join("; ") || service.path}>
                          {service.trouble.length ? service.trouble[0] : [service.location, path].filter(Boolean).join(" · ") || "Network recorded"}
                        </small>
                      </>
                    ) : (
                      <small>{network?.in_use ? "No network record yet" : ""}</small>
                    )}
                  </span>
                  <span className="c360-service-side">
                    <StatusChip value={asset.status} />
                    <small>{price === null ? "" : `${formatValue(price, "money")}/mo`}</small>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
