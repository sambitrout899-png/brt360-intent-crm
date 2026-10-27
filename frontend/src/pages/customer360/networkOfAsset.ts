import type { Asset } from "../../api/types";
import type { CustomerNetwork, CustomerNetworkService } from "../../api/c360Types";

/** The services an installed asset is delivered by: the service record in Network Inventory (linked when
 *  fulfillment created the asset, else through the asset's orders). */
export function servicesForAsset(network: CustomerNetwork | null, asset: Asset): CustomerNetworkService[] {
  if (!network?.in_use) return [];
  const orders = new Set((asset.order_history ?? []).map((h) => h.order_id));
  const linked = network.services.filter((s) => s.crm_assets.includes(asset.id));
  return linked.length ? linked : network.services.filter((s) => s.order_id != null && orders.has(s.order_id));
}