"""Licensing and training security/behavior regression tests."""
import datetime as dt
import pytest
from app.models.tenant import Tenant
from app.models.auth import User, UserRole
from app.models.training import TrainingSession, TrainingInvitation, TrainingResponse, TrainingDelivery
from app.timeutils import utcnow


def setup_training(client):
    with client.TestSessionLocal() as db:
        tenant = db.query(Tenant).first()
        tenant.licensed_modules = ['training_management']
        user = db.query(User).filter_by(role=UserRole.admin).first()
        uid = user.id
        db.commit()
    return uid


def make_session(client):
    uid = setup_training(client)
    program = client.post('/api/training/programs', json={'name': 'Safety', 'curriculum': ['Safe working'],
        'questions': [{'key':'q1','prompt':'Which is safe?', 'options':['A','B'], 'correct':1,'outcome':'Safety'}]})
    assert program.status_code == 201, program.text
    dates = {'starts_at': (utcnow()+dt.timedelta(hours=2)).isoformat()+'Z', 'ends_at': (utcnow()+dt.timedelta(hours=3)).isoformat()+'Z'}
    session = client.post('/api/training/sessions', json={'title':'Safety session', 'revision_id':program.json()['revision_id'], 'facilitator_id':uid, **dates})
    assert session.status_code == 201, session.text
    return uid, session.json()['id'], program.json()


def test_licensing_activation_and_direct_api_guards(client):
    uid = setup_training(client)
    assert client.get('/api/training/workspace').status_code == 200
    for path in ['/api/catalog', '/api/admin/flows', '/api/admin/pages', '/api/admin/custom-fields', '/api/admin/product-specifications']:
        response = client.get(path)
        if response.status_code != 404:
            assert response.status_code == 403, (path, response.text)
    pages = client.get('/api/access/me').json()['pages']
    assert 'training' in pages and 'customer' not in pages
    with client.TestSessionLocal() as db:
        tenant = db.query(Tenant).first(); tenant.licensed_modules=[]; db.commit()
    assert client.get('/api/training/workspace').status_code == 403
    with client.TestSessionLocal() as db:
        tenant=db.query(Tenant).first(); tenant.licensed_modules=['training_management','flow_studio']; db.commit()
    assert client.get('/api/training/workspace').status_code == 200
    assert client.get('/api/admin/flows').status_code == 200


def test_platform_create_and_change_licenses(client):
    with client.TestSessionLocal() as db:
        platform = db.query(User).filter_by(role=UserRole.platform_admin).first().id
    headers={'X-Test-User-Id':str(platform)}
    payload={'name':'Learning Org','slug':'learning','admin_email':'learn@example.test','admin_password':'password123','licensed_modules':['training_management']}
    response=client.post('/api/platform/tenants',json=payload,headers=headers)
    assert response.status_code == 201, response.text
    tid=response.json()['id']
    assert response.json()['licensed_modules']==['training_management']
    assert client.put(f'/api/platform/tenants/{tid}',json={'licensed_modules':[]}).status_code==403
    assert client.put(f'/api/platform/tenants/{tid}',json={'licensed_modules':['bogus']},headers=headers).status_code==422
    assert client.put(f'/api/platform/tenants/{tid}',json={'licensed_modules':[]},headers=headers).json()['licensed_modules']==[]


def test_assessment_single_use_anonymous_and_outcomes(client):
    uid,sid,_=make_session(client)
    response=client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid})
    assert response.status_code==201,response.text
    assert client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid}).status_code==409
    inbox=client.get('/api/training/workspace').json()['inbox']
    token=inbox[0]['link'].split('/')[-1]
    form=client.get(f'/api/assessments/{token}')
    assert form.status_code==200
    assert 'correct' not in form.json()['questions'][0]
    body={'answers':{'q1':1},'confidence':4,'feedback':'Useful','anonymous':True}
    assert client.post(f'/api/assessments/{token}',json={**body,'answers':{}}).status_code==422
    assert client.post(f'/api/assessments/{token}',json=body).json()['score']==100
    assert client.post(f'/api/assessments/{token}',json=body).status_code==410
    with client.TestSessionLocal() as db:
        row=db.query(TrainingResponse).one()
        assert row.user_id is None and row.sys_created_by is None and row.sys_created_at is None
    report=client.get(f'/api/training/sessions/{sid}/outcomes').json()
    assert report['phases']['pre']['knowledge']==100
    assert report['matched_count']==0


def test_post_assessment_automation_and_revision_freeze(client):
    uid,sid,program=make_session(client)
    rid=client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid}).json()['id']
    original=client.get('/api/training/workspace').json()['sessions'][0]['revision_id']
    revision=client.post(f"/api/training/programs/{program['id']}/revisions",json={'curriculum':['New'], 'questions':[{'key':'q2','prompt':'New?', 'options':['A','B'],'correct':0}]})
    assert revision.status_code==201
    assert client.get('/api/training/workspace').json()['sessions'][0]['revision_id']==original
    with client.TestSessionLocal() as db:
        session=db.get(TrainingSession,sid); session.ends_at=utcnow()-dt.timedelta(hours=1); session.starts_at=utcnow()-dt.timedelta(hours=2); db.commit()
    assert client.put(f'/api/training/registrations/{rid}/attendance',json={'attendance':'present'}).status_code==200
    for _ in range(2): assert client.post('/api/training/automation/run').status_code==200
    with client.TestSessionLocal() as db:
        assert db.query(TrainingInvitation).filter_by(phase='post').count()==1
        assert db.query(TrainingDelivery).filter_by(kind='invitation').count()==2


def test_cross_tenant_and_training_roles(client):
    uid,sid,_=make_session(client)
    with client.TestSessionLocal() as db:
        other=Tenant(name='Other',slug='other',licensed_modules=['training_management']); db.add(other); db.flush()
        outsider=User(tenant_id=other.id,name='Other',email='other@example.test',password_hash='x',role=UserRole.admin)
        viewer=User(tenant_id=db.get(User,uid).tenant_id,name='Learner',email='learner@example.test',password_hash='x',role=UserRole.viewer)
        db.add_all([outsider,viewer]); db.flush(); oid,vid=outsider.id,viewer.id; db.commit()
    assert client.get(f'/api/training/sessions/{sid}/outcomes',headers={'X-Test-User-Id':str(oid)}).status_code==404
    assert client.put('/api/training/members',json={'user_id':oid,'role':'hr'}).status_code==404
    h={'X-Test-User-Id':str(vid)}
    assert client.post('/api/training/automation/run',headers=h).status_code==403
    assert client.get(f'/api/training/sessions/{sid}/outcomes',headers=h).status_code==403
    assert client.put('/api/training/members',json={'user_id':vid,'role':'hr'},headers=h).status_code==403
    assert client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':vid},headers=h).status_code==201
    assert client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid},headers=h).status_code==403


def test_revocation_and_cancel_invalidate_assessment(client):
    uid,sid,_=make_session(client)
    client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid})
    token=client.get('/api/training/workspace').json()['inbox'][0]['link'].split('/')[-1]
    with client.TestSessionLocal() as db:
        t=db.query(Tenant).first();t.licensed_modules=[];db.commit()
    assert client.get(f'/api/assessments/{token}').status_code==403
    with client.TestSessionLocal() as db:
        t=db.query(Tenant).first();t.licensed_modules=['training_management'];db.commit()
    client.post(f'/api/training/sessions/{sid}/cancel')
    assert client.get(f'/api/assessments/{token}').status_code==410


def test_shared_page_designer_validation_and_flow_rollback(client):
    import json
    from app.models.flows import FlowDefinition
    from app.models.training import TrainingProgram
    from app.models.custom_fields import CustomFieldDefinition
    setup_training(client)
    with client.TestSessionLocal() as db:
        tid=db.query(Tenant).first().id
        db.add(CustomFieldDefinition(tenant_id=tid,entity_type='training_program',key='audience',label='Audience',data_type='text',required=True))
        db.commit()
    payload={'name':'Safety','curriculum':['Safety'], 'questions':[{'key':'q','prompt':'Safe?', 'options':['A','B'],'correct':0}]}
    assert client.post('/api/training/programs',json=payload).status_code==422
    payload['details']={'audience':'New joiners'}
    assert client.post('/api/training/programs',json=payload).status_code==201
    graph={'nodes':[{'id':'s','type':'start','config':{}},{'id':'r','type':'reject','config':{'message':'Training is paused'}}], 'edges':[{'id':'e','source':'s','target':'r'}]}
    with client.TestSessionLocal() as db:
        db.add(FlowDefinition(tenant_id=tid,name='Training pause',trigger_type='intent',trigger_intent_type='TRN-01',timing='before',graph_json=json.dumps(graph),is_active=True))
        db.commit()
    response=client.post('/api/training/intents',json={'code':'TRN-01','entities':payload})
    assert response.status_code==422,response.text
    with client.TestSessionLocal() as db:
        assert db.query(TrainingProgram).count()==1


def test_reminder_idempotency_expiry_and_schedule_conflict(client):
    uid,sid,program=make_session(client)
    session=client.get('/api/training/workspace').json()['sessions'][0]
    duplicate={'title':'Conflict','revision_id':program['revision_id'],'facilitator_id':uid,'starts_at':session['starts_at']+'Z','ends_at':session['ends_at']+'Z'}
    assert client.post('/api/training/sessions',json=duplicate).status_code==409
    client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid})
    for _ in range(2): client.post('/api/training/automation/run')
    with client.TestSessionLocal() as db:
        assert db.query(TrainingDelivery).filter_by(kind='reminder').count()==1
        invitation=db.query(TrainingInvitation).first()
        invitation.expires_at=utcnow()-dt.timedelta(seconds=1)
        token=db.query(TrainingDelivery).first().link.split('/')[-1]
        db.commit()
    assert client.get(f'/api/assessments/{token}').status_code==410


def test_qr_and_page_authoring(client, monkeypatch):
    from app.config import get_settings
    uid,sid,_=make_session(client)
    with client.TestSessionLocal() as db:
        t=db.query(Tenant).first(); t.licensed_modules=['training_management','page_designer','flow_studio'];db.commit()
    response=client.get('/api/admin/pages')
    assert response.status_code==200,response.text
    assert any(e['key']=='training_program' for e in response.json()['entities'])
    assert client.get('/api/pages/training_program/form').status_code==200
    assert client.get('/api/pages/party/form').status_code==403
    catalog=client.get('/api/admin/flows/catalog').json()
    assert any(i['code']=='TRN-01' for i in catalog['intents'])
    client.post(f'/api/training/sessions/{sid}/registrations',json={'user_id':uid})
    token=client.get('/api/training/workspace').json()['inbox'][0]['link'].split('/')[-1]
    monkeypatch.setattr(get_settings(),'training_public_origin','https://learning.example.test')
    qr=client.get(f'/api/assessments/{token}/qr')
    assert qr.status_code==200,qr.text
    assert qr.content.startswith(b'\x89PNG')
    assert qr.headers['cache-control']=='no-store'


def test_alternate_entry_points_cannot_bypass_licenses(client):
    setup_training(client)
    assert client.get('/api/intents/lookup?record_type=party').status_code==403
    assert client.get('/api/pages/offerings').status_code==403
    assert client.post('/api/assistant/scratch/build',json={'spec':{}}).status_code==403
    assert client.post('/api/intents/prepare',json={'intent_type':'INT-01','entities':{}}).status_code==403
    catalog=client.get('/api/intents/catalog').json()
    assert not any(i['code'].startswith('INT-') for i in catalog['intents'])
