import json
import pytest
from test_offering_order_intent import setup_order, invoke
from app.models.orders import Order, OrderItem, OrderStatus
from app.models.fulfillment import Asset
from app.models.abo import AssetChange, AssetRevision


def installed(client):
    values = setup_order(client)
    placed = invoke(client, 'INT-106', **values, order_mode='silent')
    assert placed['status'] == 'executed', placed
    with client.TestSessionLocal() as db:
        order = db.get(Order, placed['result']['order_id'])
        assert order.status == OrderStatus.completed
        assert all(i.status == 'completed' for i in order.items)
        asset = db.query(Asset).filter_by(order_item_id=order.items[0].id).one()
        assert asset.service_account_id == order.service_account_id
        assert json.loads(asset.instance_json)['configuration'] is not None
        return asset.id, order.id


@pytest.mark.parametrize('operation', ['modify', 'disconnect', 'terminate', 'suspend', 'number_portability'])
def test_change_creates_order_and_updates_same_asset_only_on_completion(client, operation):
    asset_id, source_order = installed(client)
    changed = invoke(client, 'INT-107', asset_id=asset_id, asset_operation=operation, port_number='9876543210', donor_operator='Example', quantity=2)
    assert changed['status'] == 'executed', changed
    change_order = changed['result']['order_id']
    assert change_order != source_order
    with client.TestSessionLocal() as db:
        assert db.get(Asset, asset_id).version == 1
        from app.orchestration.fulfillment_ops import op_create_asset
        pending = db.get(Order, change_order)
        result = op_create_asset(db, pending.tenant_id, pending.service_account_id,
                                 pending.items[0].id, 'ignored', 'ignored')
        assert result['asset_id'] == asset_id
        assert db.get(Order, source_order).monthly_price == 500
        assert db.query(Asset).count() == 1
    done = invoke(client, 'INT-108', order_id=change_order)
    assert done['status'] == 'executed', done
    repeated = invoke(client, 'INT-108', order_id=change_order)
    assert repeated['status'] == 'executed', repeated
    with client.TestSessionLocal() as db:
        assert db.get(Asset, asset_id).version == 2
        assert db.query(Asset).count() == 1
        assert db.query(AssetRevision).filter_by(asset_id=asset_id).count() == 2
        assert db.get(Order, source_order).monthly_price == 500


def test_cannot_change_original_or_overlap_requests(client):
    asset, source = installed(client)
    one = invoke(client, 'INT-107', asset_id=asset, asset_operation='suspend')
    assert one['status'] == 'executed', one
    two = invoke(client, 'INT-107', asset_id=asset, asset_operation='modify')
    assert two['status'] == 'failed', two
    with client.TestSessionLocal() as db:
        db.get(Order, source).monthly_price = 1
        with pytest.raises(ValueError, match='read-only'):
            db.flush()
        db.rollback()


@pytest.mark.parametrize('operation', ['relocate', 'customer_transfer'])
def test_transfer_changes_account_only_after_fulfillment(client, operation):
    from app.models.accounts import ServiceAccount
    from app.models.orders import BillingAccount
    from app.models.crm import Party
    asset_id, original = installed(client)
    with client.TestSessionLocal() as db:
        source = db.get(Order, original)
        party_id = source.party_id
        if operation == 'customer_transfer':
            p = Party(tenant_id=1, name='New asset owner', party_type='organization')
            db.add(p)
            db.flush()
            party_id = p.id
        from app.integration.order_adapter import get_or_create_billing_account
        billing = get_or_create_billing_account(db, 1, party_id)
        target = ServiceAccount(tenant_id=1, billing_account_id=billing.id, name='Destination site')
        db.add(target)
        db.commit()
        target_id, source_id = target.id, source.service_account_id
    changed = invoke(client, 'INT-107', asset_id=asset_id, asset_operation=operation,
                     target_service_account_id=target_id, target_site='Branch B')
    assert changed['status'] == 'executed', changed
    with client.TestSessionLocal() as db:
        assert db.get(Asset, asset_id).service_account_id == source_id
    done = invoke(client, 'INT-108', order_id=changed['result']['order_id'])
    assert done['status'] == 'executed', done
    with client.TestSessionLocal() as db:
        if operation == 'relocate':  # Siebel: disconnected at the old site, a new asset connected at the new one
            old = db.get(Asset, asset_id)
            assert old.service_account_id == source_id and old.status.value == 'decommissioned'
            asset_id = json.loads(old.instance_json)['replaced_by_asset_id']
        assert db.get(Asset, asset_id).service_account_id == target_id
        assert db.get(Order, original).service_account_id == source_id


def test_suspend_resume_preserves_original(client):
    asset_id, original = installed(client)
    for operation, expected in [('suspend', 'suspended'), ('resume', 'active')]:
        changed = invoke(client, 'INT-107', asset_id=asset_id, asset_operation=operation)
        assert changed['status'] == 'executed', changed
        done = invoke(client, 'INT-108', order_id=changed['result']['order_id'])
        assert done['status'] == 'executed', done
        with client.TestSessionLocal() as db:
            assert db.get(Asset, asset_id).status.value == expected
            assert db.get(Order, original).status == OrderStatus.completed


def test_multi_line_completion_and_catalog_changes_do_not_mutate_assets(client):
    from app.crm_core import abo_service
    from app.models.catalog import ProductOffering
    values = setup_order(client)
    placed = invoke(client, 'INT-106', **values, order_mode='interactive', submit_for_provisioning=True)
    assert placed['status'] == 'executed', placed
    with client.TestSessionLocal() as db:
        source = db.get(Order, placed['result']['order_id'])
        offering = db.get(ProductOffering, source.items[0].product_offering_id)
        from app.integration import order_adapter
        from app.models.orders import OrderType
        order = order_adapter.create_order(db, tenant_id=1, party_id=source.party_id, service_account_id=source.service_account_id,
                                           quote_id=None, order_type=OrderType.new, monthly_price=1000)
        for _ in range(2):
            item = order_adapter.add_order_item(db, order.id, offering.id)
            item.net_unit_price = 500
        abo_service.complete(db, order)
        snapshots = [a.instance_json for a in db.query(Asset).filter(Asset.order_item_id.in_([i.id for i in order.items])).order_by(Asset.id)]
        assert len(snapshots) == 2
        offering.name = 'Renamed catalog product'
        db.flush()
        abo_service.complete(db, order)
        assert snapshots == [a.instance_json for a in db.query(Asset).filter(Asset.order_item_id.in_([i.id for i in order.items])).order_by(Asset.id)]
