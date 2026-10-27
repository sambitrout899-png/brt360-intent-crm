import json
import pytest
from test_asset_ordering import installed
from app.models.fulfillment import Asset, AssetStatus
from app.models.orders import Order, OrderType
from app.models.accounts import ServiceAccount
from app.crm_core import abo_service, comm_service, comm_billing
from app.orchestration.fulfillment_ops import op_create_asset


def test_missing_work_order_line_reuses_product_asset(client):
    asset_id, order_id = installed(client)
    with client.TestSessionLocal() as db:
        order = db.get(Order, order_id)
        for _ in range(2):
            out = op_create_asset(db, 1, order.service_account_id, None, "Service", f"ORDER-{order.id}-ITEM-0")
            assert out['asset_id'] == asset_id
        assert db.query(Asset).count() == 1


def test_legacy_placeholder_reconciled_without_merging_real_subscriptions(client):
    asset_id, order_id = installed(client)
    with client.TestSessionLocal() as db:
        source = db.get(Asset, asset_id)
        old = Asset(tenant_id=1, service_account_id=source.service_account_id, asset_type='Service',
                    identifier=f'ORDER-{order_id}-ITEM-0', status=AssetStatus.active, inventory_system_ref='legacy-reference')
        equipment = Asset(tenant_id=1, service_account_id=source.service_account_id, asset_type='Service', identifier='independent-equipment', status=AssetStatus.active)
        db.add_all([old, equipment]); db.flush()
        abo_service.reconcile_placeholders(db, 1)
        assert old.superseded_by_asset_id == asset_id
        assert old.status == AssetStatus.decommissioned
        assert equipment.superseded_by_asset_id is None
        assert db.get(Asset, old.id) is old  # audit record is retained
        party_id = db.get(Order, order_id).party_id
        assert {a.id for a in abo_service.customer_assets(db, 1, party_id)} == {asset_id, equipment.id}
        abo_service.reconcile_placeholders(db, 1)
        assert db.query(Asset).count() == 3


def test_adopt_existing_placeholder_instead_of_creating_second_asset(client):
    from app.integration import order_adapter
    _, origin_id = installed(client)
    with client.TestSessionLocal() as db:
        source = db.get(Order, origin_id)
        order = order_adapter.create_order(db, tenant_id=1, party_id=source.party_id, service_account_id=source.service_account_id,
                                          quote_id=None, order_type=OrderType.new, monthly_price=500)
        item = order_adapter.add_order_item(db, order.id, source.items[0].product_offering_id)
        old = Asset(tenant_id=1, service_account_id=order.service_account_id, asset_type='Service', identifier=f'ORDER-{order.id}-ITEM-0', status=AssetStatus.active)
        db.add(old); db.flush(); old_id = old.id
        abo_service.complete(db, order)
        assert db.query(Asset).filter_by(order_item_id=item.id).one().id == old_id
        assert old.asset_type != 'Service'
        assert json.loads(old.instance_json)['order_item_id'] == item.id


def test_billing_and_service_branches_link_to_customer(client):
    _, order_id = installed(client)
    with client.TestSessionLocal() as db:
        order = db.get(Order, order_id)
        sa = db.get(ServiceAccount, order.service_account_id)
        assert sa.party_id == order.party_id
        out = comm_service.save_billing_profile(db, 1, {'party_id': order.party_id, 'profile_name': 'Office billing'})
        profile = comm_service.billing_profiles(db, 1, order.party_id)[0]
        assert profile.billing_account_id == sa.billing_account_id
        assert out['profile']['billing_account_id'] == sa.billing_account_id
        portal = comm_billing.portal(db, 1, order.party_id)
        assert portal['billing_account']['id'] == sa.billing_account_id
        assert portal['billing_profiles'][0]['billing_account_id'] == sa.billing_account_id
        assert comm_billing.unbilled(db, 1, order.party_id)['recurring'] > 0


def test_billing_uses_updated_asset_once_after_change_order(client):
    from test_offering_order_intent import invoke
    asset_id, order_id = installed(client)
    changed = invoke(client, 'INT-107', asset_id=asset_id, asset_operation='modify', quantity=2)
    assert changed['status'] == 'executed', changed
    done = invoke(client, 'INT-108', order_id=changed['result']['order_id'])
    assert done['status'] == 'executed', done
    with client.TestSessionLocal() as db:
        order = db.get(Order, order_id)
        services = comm_service.live_orders(db, 1, order.party_id)
        assert len(services) == 1
        assert services[0].monthly_price == 1000
        assert order.monthly_price == 500
