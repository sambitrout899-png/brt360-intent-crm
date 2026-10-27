"""Shared authoring tools must not expose unrelated functional modules."""
import json
from app.models.tenant import Tenant
from app.models.flows import FlowDefinition
from app.models.pages import PageLayout, ValidationRule
from app.models.intent_catalog import IntentSetting

TOOLS = ['custom_development', 'flow_studio', 'page_designer']


def select_modules(client, *modules):
    with client.TestSessionLocal() as db:
        tenant = db.query(Tenant).first()
        tenant.licensed_modules = [*TOOLS, *modules]
        db.commit()
        return tenant.id


def test_training_only_filters_all_admin_surfaces(client):
    tid = select_modules(client, 'training_management')
    with client.TestSessionLocal() as db:
        db.add(PageLayout(tenant_id=tid, page_key='party.legacy', entity_type='party', name='Legacy CRM page', is_default=False))
        db.add(FlowDefinition(tenant_id=tid, name='Legacy CRM action', entity_type='party', trigger_type='custom_intent', trigger_phrases='legacy action', graph_json='{}'))
        db.add(ValidationRule(tenant_id=tid, entity_type='party', name='CRM rule', source_text='Must have name', kind='simple', compiled_json='{}'))
        db.commit()
    catalog = client.get('/api/admin/intents').json()
    assert {i['code'] for i in catalog['intents']} == {'INT-103'}
    assert not catalog['entity_types']
    assert not {'sales','billing','products'} & {c['key'] for c in catalog['categories']}
    pages = client.get('/api/admin/pages').json()
    assert {p['entity_type'] for p in pages['pages']} == {'training_program'}
    assert {e['key'] for e in pages['entities']} == {'training_program'}
    assert not pages['segments']
    assert client.get('/api/admin/pages/rules/all').json()['rules'] == []
    for suffix in ['', '/vanilla', '/restore']:
        graph = client.get('/api/admin/function-map' + suffix).json()
        codes = {n['code'] for n in graph['nodes']}
        assert codes == {'INT-103'}
        assert all(e['source'] in codes and e['target'] in codes for e in graph['edges'])
        assert all(set(p['steps']) <= codes for p in graph['processes'])
        assert {lane['key'] for lane in graph['lanes']} == {'workspace'}
    assert client.get('/api/admin/flows').json() == []
    triggers = client.get('/api/admin/flows/catalog').json()['intents']
    assert all(i['code']=='INT-103' or i['code'].startswith('TRN-') for i in triggers)


def test_direct_edit_paths_reject_unlicensed_content(client):
    select_modules(client, 'training_management')
    assert client.put('/api/admin/intents/INT-01',json={'label':'Should not change'}).status_code==404
    assert client.post('/api/admin/intents/INT-01/reset').status_code==404
    assert client.get('/api/admin/pages/catalog/party').status_code==403
    assert client.post('/api/admin/pages',json={'entity_type':'party','name':'No'}).status_code==403
    assert client.delete('/api/admin/pages/party.B2B').status_code==404
    assert client.post('/api/admin/function-map/apply',json={'nodes':[{'code':'INT-01','active':False}],'edges':[]}).status_code==403


def test_module_reactivation_restores_pages_settings_and_map_layout(client):
    tid=select_modules(client, 'telecom_crm', 'training_management')
    client.put('/api/admin/intents/INT-01',json={'label':'My product launch'})
    graph=client.get('/api/admin/function-map').json()
    node=next(n for n in graph['nodes'] if n['code']=='INT-01')
    node['x']=777
    response=client.post('/api/admin/function-map/apply',json={'nodes':graph['nodes'],'edges':graph['edges']})
    assert response.status_code==200,response.text
    with client.TestSessionLocal() as db:
        db.add(PageLayout(tenant_id=tid,page_key='party.saved',entity_type='party',name='Saved CRM page',is_default=False))
        db.commit()
    select_modules(client,'training_management')
    filtered=client.get('/api/admin/function-map').json()
    assert client.post('/api/admin/function-map/apply',json={'nodes':filtered['nodes'],'edges':filtered['edges']}).status_code==200
    select_modules(client,'telecom_crm','training_management')
    graph=client.get('/api/admin/function-map').json()
    node=next(n for n in graph['nodes'] if n['code']=='INT-01')
    assert node['label']=='My product launch' and node['active'] and node['x']==777
    assert any(p['page_key']=='party.saved' for p in client.get('/api/admin/pages').json()['pages'])
    assert any(e['source']=='INT-15' and e['target']=='INT-46' for e in graph['edges'])


def test_telecom_only_and_no_functional_modules(client):
    select_modules(client,'telecom_crm')
    assert all(p['entity_type']!='training_program' for p in client.get('/api/admin/pages').json()['pages'])
    assert not any(i['code'].startswith('TRN-') for i in client.get('/api/admin/flows/catalog').json()['intents'])
    select_modules(client)
    assert client.get('/api/admin/pages').json()['pages']==[]
    assert {i['code'] for i in client.get('/api/admin/intents').json()['intents']}=={'INT-103'}



def test_custom_flow_action_cannot_leak_unselected_module(client):
    tid=select_modules(client,'training_management')
    graph={'nodes':[{'id':'s','type':'start','config':{}},{'id':'a','type':'action','config':{'intent':'INT-01'}},{'id':'e','type':'end','config':{}}],
           'edges':[{'source':'s','target':'a'},{'source':'a','target':'e'}]}
    with client.TestSessionLocal() as db:
        flow=FlowDefinition(tenant_id=tid,name='Hidden Telecom action',entity_type=None,trigger_type='custom_intent',trigger_phrases='hidden launch',graph_json=json.dumps(graph))
        db.add(flow);db.commit();fid=flow.id
    assert not any(i['code']==f'FLOW-{fid}' for i in client.get('/api/admin/intents').json()['intents'])
    assert client.get(f'/api/admin/flows/{fid}').status_code==404
    assert client.get('/api/admin/flows/field-catalog?entity_type=party').status_code==403
    assert client.get('/api/admin/flows/field-catalog?trigger_intent_type=INT-01').status_code==403
    select_modules(client,'telecom_crm','training_management')
    assert any(i['code']==f'FLOW-{fid}' for i in client.get('/api/admin/intents').json()['intents'])


def test_first_filtered_map_save_preserves_other_modules_default_connections(client):
    select_modules(client,'training_management')
    graph=client.get('/api/admin/function-map').json()
    assert client.post('/api/admin/function-map/apply',json={'nodes':graph['nodes'],'edges':graph['edges']}).status_code==200
    select_modules(client,'telecom_crm')
    graph=client.get('/api/admin/function-map').json()
    assert any(e['source']=='INT-15' and e['target']=='INT-46' for e in graph['edges'])
