import pytest
from app.crm_core import page_service, process_service
from app.models.crm import Party
from app.models.pages import PageLayout
import json


def test_explicit_page_cannot_cross_segment_or_entity(client):
    with client.TestSessionLocal() as db:
        page_service.save_page(db, 1, 'party', 'B2C', page_service.default_layout(db, 1, 'party', 'B2C'))
        assert page_service.page(db, 1, 'party', 'B2B', 'party.B2C')['segment'] == 'B2B'
        assert page_service.page(db, 1, 'lead', 'B2B', 'party.B2C')['entity_type'] == 'lead'


def test_existing_general_page_journey_is_hidden_for_other_segment(client):
    with client.TestSessionLocal() as db:
        layout = {'sections': [{'title': 'B2C process', 'fields': [
            {'key': 'journey_old'}, {'kind': 'text', 'id': 'help', 'text': 'Consumer journey'},
            {'kind': 'button', 'id': 'confirm', 'label': 'Confirm', 'intent': 'INT-104'}]}]}
        db.add(PageLayout(tenant_id=1, entity_type='party', page_key='party', name='General', layout_json=json.dumps(layout), is_active=True, is_default=True))
        db.flush()
        b2b = page_service.form(db, 1, 'party', 'B2B')
        assert not any(s['title'] == 'B2C process' for s in b2b['sections'])


def test_record_segment_cannot_be_overridden_by_form_query(client):
    with client.TestSessionLocal() as db:
        party = Party(tenant_id=1, name='Business', party_type='organization')
        db.add(party)
        process_service.party_profile(party, create=True).segment = 'B2B'
        db.commit()
        record_id = party.id
    result = client.get(f'/api/pages/party/form?record_id={record_id}&segment=B2C&page_key=party.B2C')
    assert result.status_code == 200, result.text
    assert result.json()['segment'] == 'B2B'


def test_legacy_journey_execution_rejects_business_customer(client):
    from app.models.flows import FlowDefinition
    from app.orchestration import flow_engine
    with client.TestSessionLocal() as db:
        party = Party(tenant_id=1, name='Business', party_type='organization')
        db.add(party)
        process_service.party_profile(party, create=True).segment = 'B2B'
        db.flush()
        graph = {'nodes': [{'id': 's', 'type': 'start'}, {'id': 'c', 'type': 'set_field', 'config': {'field': 'record.journey_old', 'value': 'done'}}, {'id': 'e', 'type': 'end'}],
                 'edges': [{'source': 's', 'target': 'c'}, {'source': 'c', 'target': 'e'}]}
        flow = FlowDefinition(tenant_id=1, name='B2C process: confirm', entity_type='party', trigger_type='custom_intent', graph_json=json.dumps(graph))
        with pytest.raises(flow_engine.FlowRejected, match='only to B2C'):
            flow_engine.run_flow(db, 1, None, flow, {'entities': {'party_id': party.id}})


@pytest.mark.parametrize("trigger", ["intent", "state_enter", "custom_intent"])
def test_legacy_named_flow_cannot_run_for_other_segment(client, trigger):
    from app.models.flows import FlowDefinition
    from app.orchestration import flow_engine

    with client.TestSessionLocal() as db:
        party = Party(tenant_id=1, name="Business customer", party_type="organization")
        db.add(party)
        process_service.party_profile(party, create=True).segment = "B2B"
        db.flush()
        graph = {"nodes": [{"id": "start", "type": "start"}, {"id": "end", "type": "end"}],
                 "edges": [{"source": "start", "target": "end"}]}
        flow = FlowDefinition(tenant_id=1, name="B2C Create Order", entity_type="party",
                              trigger_type=trigger, graph_json=json.dumps(graph))
        context = {"entities": {"party_id": party.id, "segment": "B2C"}}
        if trigger == "custom_intent":
            with pytest.raises(flow_engine.FlowRejected, match="only to B2C"):
                flow_engine.run_flow(db, 1, None, flow, context)
        else:
            result = flow_engine.flow_step(db, 1, None, flow, lambda: context).action()
            assert "flows_run" not in result
            assert result["flows_skipped"][0]["skipped"] is True
        assert process_service.party_segment(party) == "B2B"


def test_flow_scope_uses_explicit_metadata_before_legacy_name():
    from app.orchestration.process_journey import scope

    assert scope({}, "B2C Create Order") == "B2C"
    assert scope({}, "b2b approval") == "B2B"
    assert scope({}, "B2B and B2C notification") is None
    assert scope({"segment": "B2B"}, "B2C old name") == "B2B"
