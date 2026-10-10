import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, proactive, unified
from app.core.database import Base, get_db
from app.models.proactive import ProactiveSession, ProactiveAudit
from app.models import auth as auth_models  # noqa: F401
from app.models import destination, hotel, knowledge, unified as unified_models  # noqa: F401
from app.services.proactive.controller import initial_state
from app.services.demo_seed import seed_unified_demo


class ProactiveAPITests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
        self.Session=sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        with self.Session() as db: seed_unified_demo(db)
        app=FastAPI();app.include_router(auth.router);app.include_router(unified.router);app.include_router(proactive.router)
        def db_override():
            with self.Session() as db: yield db
        app.dependency_overrides[get_db]=db_override
        self.client=TestClient(app)
        auth_session=self.client.get('/api/v1/auth/session')
        self.client.headers.update({'X-CSRF-Token':auth_session.json()['csrf_token']})
        self.patches=[patch.object(proactive.settings,'proactive_research_enabled',True),
                      patch.object(proactive.settings,'proactive_fixture_enabled',True),
                      patch.object(proactive.settings,'proactive_shadow_enabled',True),
                      patch.object(proactive.settings,'app_env','development')]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.client.close();self.engine.dispose()

    def session(self):
        r=self.client.post('/api/v1/proactive/sessions');self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['mode'],'OFF')
        return {'X-Research-Token':r.json()['token']}

    def call(self,path,headers,json):
        r=self.client.post('/api/v1/proactive'+path,headers=headers,json=json)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def mode(self,h,mode):
        return self.client.put('/api/v1/proactive/mode',headers=h,json={'mode':mode,'consent':mode!='OFF'})

    def test_end_to_end_off_on_offer_chat_off_isolation(self):
        a,b=self.session(),self.session()
        with patch('app.services.proactive.controller.decide',side_effect=AssertionError('OFF evaluated')):
            self.assertEqual(self.call('/poll',a,{'attention_available':True})['status'],'OFF')
        conversation=self.client.post('/api/v1/conversations',json={}).json()['id']
        for mode in ('OFF','LIVE','OFF'):
            self.assertEqual(self.mode(a,mode).status_code,200)
            with patch('app.api.unified.persist_turn'):
                r=self.client.post(f'/api/v1/conversations/{conversation}/messages/stream',json={'message':'Xin chào','mode':'TRAVEL'})
            self.assertIn('event: done',r.text)
            if mode=='LIVE':
                self.call('/fixture',a,{'scenario':'offer'})
                result=self.call('/poll',a,{'attention_available':True})
                self.assertEqual(result['event']['kind'],'OFFER')
                event=result['event']
                self.assertEqual(self.call('/poll',a,{'attention_available':True})['event']['id'],event['id'])
                self.call('/receipt',a,{'event_id':event['id'],'attention_available':True})
                self.assertIsNone(self.call('/poll',a,{'attention_available':True})['event'])
        self.assertIsNone(self.call('/poll',a,{'attention_available':True})['event'])
        self.assertEqual(self.call('/poll',b,{'attention_available':True})['status'],'OFF')

    def test_shadow_logs_without_delivery(self):
        h=self.session();self.mode(h,'SHADOW');self.call('/fixture',h,{'scenario':'offer'})
        self.assertIsNone(self.call('/poll',h,{'attention_available':True})['event'])
        with self.Session() as db:
            records=[r.record for r in db.query(ProactiveAudit).all()]
        decision=next(r for r in records if r['kind']=='decision')
        self.assertTrue(decision['should_intervene']);self.assertEqual(decision['suppression_reason'],'SHADOW')
        self.assertNotIn(h['X-Research-Token'],str(records))

    def test_authentication_and_cross_session_event(self):
        a,b=self.session(),self.session()
        self.assertEqual(self.client.get('/api/v1/proactive/mode').status_code,401)
        self.assertEqual(self.client.get('/api/v1/proactive/mode',headers={'X-Research-Token':'wrong'}).status_code,401)
        self.mode(a,'LIVE');self.call('/fixture',a,{'scenario':'offer'})
        event=self.call('/poll',a,{'attention_available':True})['event']
        self.assertEqual(self.call('/receipt',b,{'event_id':event['id'],'attention_available':True})['status'],'SUPPRESSED')

    def test_no_calibration_and_production_fixture_disabled(self):
        h=self.session();self.mode(h,'LIVE')
        self.assertEqual(self.call('/poll',h,{'attention_available':True})['status'],'NOT_CONFIGURED')
        with patch.object(proactive.settings,'app_env','production'):
            self.assertEqual(self.client.post('/api/v1/proactive/fixture',headers=h,json={}).status_code,404)
        self.assertEqual(self.client.put('/api/v1/proactive/mode',headers=h,json={'mode':'LIVE','consent':False}).status_code,409)

    def test_disable_suppresses_pending_and_kill_switch(self):
        h=self.session();self.mode(h,'LIVE');self.call('/fixture',h,{'scenario':'offer'})
        event=self.call('/poll',h,{'attention_available':True})['event']
        self.mode(h,'OFF')
        self.assertEqual(self.call('/receipt',h,{'event_id':event['id'],'attention_available':True})['status'],'SUPPRESSED')
        with patch.object(proactive.settings,'proactive_research_enabled',False):
            self.assertEqual(self.call('/poll',h,{'attention_available':True})['status'],'OFF')

    def test_failure_isolated_from_chat(self):
        h=self.session();self.mode(h,'LIVE')
        with patch('app.services.proactive.controller.adapt',side_effect=RuntimeError('private must not log')):
            self.assertEqual(self.call('/poll',h,{'attention_available':True})['status'],'PROACTIVE_ERROR')
        self.assertEqual(self.client.post('/api/v1/conversations',json={}).status_code,200)

    def test_cas_conflict_preserves_first_writer(self):
        self.session()
        with self.Session() as db1,self.Session() as db2:
            row1=db1.query(ProactiveSession).first();row2=db2.query(ProactiveSession).first()
            proactive.mutate(db1,row1,lambda s: ({'ok':True},None))
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as raised:
                proactive.mutate(db2,row2,lambda s: ({'ok':True},None))
            self.assertEqual(raised.exception.status_code,409)
