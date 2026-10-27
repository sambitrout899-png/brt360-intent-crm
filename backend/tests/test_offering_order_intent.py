import pytest
from app.models.orders import Order, OrderItem


def invoke(client, code, **entities):
    return client.post('/api/intents', json={'text': '', 'structured': {'intent_type': code, 'entities': entities}}).json()


def setup_order(client):
    result = invoke(client, 'INT-01', name='Offering order fiber', composition='Fiber500', segment='B2C', channel='B2C', list_price=500, requested_discount_pct=0)
    assert result['status'] == 'executed', result
    customer = invoke(client, 'INT-41', name='Offering order customer')
    assert customer['status'] == 'executed', customer
    return {'party_id': customer['result']['party_id'], 'offering_name': 'Offering order fiber'}


def test_silent_defaults_ignore_overrides(client):
    values = setup_order(client)
    result = invoke(client, 'INT-106', **values, order_mode='silent', quantity=2,
                    requested_discount_pct=50, pricing_attributes={'invalid': 'override'})
    assert result['status'] == 'executed', result
    assert result['result']['discount_pct'] == 0
    assert result['result']['monthly_price'] == 1000
    with client.TestSessionLocal() as db:
        item = db.get(OrderItem, result['result']['order_item_id'])
        assert item.discount_pct == 0
        assert item.unit_price == item.net_unit_price == 500
        assert db.get(Order, item.order_id).quote_id is None


def test_interactive_waits_then_submits(client):
    values = setup_order(client)
    result = invoke(client, 'INT-106', **values, order_mode='interactive')
    assert result['status'] == 'executed', result
    assert result['result']['status'] == 'awaiting_configuration'
    assert 'order_id' not in result['result']
    with client.TestSessionLocal() as db:
        assert db.query(Order).count() == 0
    submitted = invoke(client, 'INT-106', **values, order_mode='interactive', submit_for_provisioning=True, requested_discount_pct=5)
    assert submitted['status'] == 'executed', submitted
    assert submitted['result']['net_unit_price'] == 475
    with client.TestSessionLocal() as db:
        assert db.get(OrderItem, submitted['result']['order_item_id']).discount_pct == 5


def test_agent_pauses_for_configuration_instead_of_reporting_order_done(client):
    values = setup_order(client)
    payload = {"goal": "Order this offering", "mode": "execute",
               "steps": [{"intent_type": "INT-106", "entities": {**values, "order_mode": "interactive"}}]}
    response = client.post("/api/agent/run", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "paused"
    assert result["stopped"]["kind"] == "needs_input"
    assert result["stopped"]["missing_fields"] == ["submit_for_provisioning"]
    with client.TestSessionLocal() as db:
        assert db.query(Order).count() == 0
    payload["steps"][0]["entities"]["submit_for_provisioning"] = True
    response = client.post("/api/agent/run", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "done", response.json()


@pytest.mark.parametrize('extra', [{'quantity': -1}, {'quantity': 1.5}, {'order_mode': 'unknown'}])
def test_invalid_inputs_do_not_create_order(client, extra):
    values = setup_order(client)
    result = invoke(client, 'INT-106', **values, **extra)
    assert result['status'] != 'executed', result
    with client.TestSessionLocal() as db:
        assert db.query(Order).count() == 0


def test_required_technical_parameters_block_silent_and_accept_configuration(client):
    values = setup_order(client)
    design = client.post('/api/intents', json={'text': 'Design Offering order fiber: Bandwidth: 100 Mbps +0, 500 Mbps +200 required default 100 Mbps; technical: VLAN ID required number'}).json()
    assert design['status'] == 'executed', design
    failed = invoke(client, 'INT-106', **values, order_mode='silent')
    assert failed['status'] == 'failed', failed
    with client.TestSessionLocal() as db:
        assert db.query(Order).count() == 0
    configured = invoke(client, 'INT-106', **values, order_mode='interactive', submit_for_provisioning=True,
                        pricing_attributes={'Bandwidth': '500 Mbps'}, technical_attributes={'VLAN ID': '120'})
    assert configured['status'] == 'executed', configured
    assert configured['result']['unit_price'] == 700
    assert configured['result']['provisioning_attributes']['VLAN ID'] == '120'


@pytest.mark.parametrize('mode', ['silent', 'interactive'])
def test_flow_uses_previous_intent_inputs(client, mode):
    values = setup_order(client)
    graph = {'nodes': [{'id': 's', 'type': 'start', 'config': {}},
                       {'id': 'a', 'type': 'action', 'config': {'intent': 'INT-106', 'on_error': 'stop', 'entities': {
                           'party_id': '{{flow.inputs.parameters.party_id}}',
                           'offering_name': '{{flow.inputs.parameters.offering_name}}', 'order_mode': mode}}},
                       {'id': 'e', 'type': 'end', 'config': {}}],
             'edges': [{'id': '1', 'source': 's', 'target': 'a'}, {'id': '2', 'source': 'a', 'target': 'e'}]}
    flow = client.post('/api/admin/flows', json={'name': 'Offering order handoff', 'entity_type': 'party', 'trigger_type': 'intent', 'trigger_intent_type': 'INT-49', 'graph': graph}).json()
    sim = client.post(f"/api/admin/flows/{flow['id']}/simulate", json={'entity_id': values['party_id'], 'entities': values}).json()
    assert sim['ok'], sim
    output = sim['run']['steps']['a']['output']
    if mode == 'silent':
        assert output['order_item_id']
    else:
        assert output['configuration_request']['entities']['party_id'] == values['party_id']
        assert sim['run']['trace'][-1]['node_id'] == 'a'


def test_after_flow_waits_for_submission_and_brt_id_is_accepted(client):
    from app.models.crm import Party
    values = setup_order(client)
    with client.TestSessionLocal() as db:
        values['party_id'] = db.get(Party, values['party_id']).brt_id
    graph = {'nodes': [{'id': 's', 'type': 'start', 'config': {}},
                       {'id': 'm', 'type': 'map', 'config': {'values': {'order_id': '{{trigger.output.order_id}}'}}},
                       {'id': 'e', 'type': 'end', 'config': {}}],
             'edges': [{'id': '1', 'source': 's', 'target': 'm'}, {'id': '2', 'source': 'm', 'target': 'e'}]}
    created = client.post('/api/admin/flows', json={'name': 'After submission only', 'trigger_type': 'intent', 'trigger_intent_type': 'INT-106', 'timing': 'after', 'graph': graph})
    assert created.status_code < 300, created.text
    assert client.post(f"/api/admin/flows/{created.json()['id']}/activate").status_code == 200
    waiting = invoke(client, 'INT-106', **values, order_mode='interactive')
    assert waiting['status'] == 'executed', waiting
    assert not waiting['result'].get('flows_run')
    submitted = invoke(client, 'INT-106', **values, order_mode='interactive', submit_for_provisioning=True)
    assert submitted['status'] == 'executed', submitted
    assert submitted['result']['flows_run'][0]['steps']['m']['output']['order_id'] == submitted['result']['order_id']
