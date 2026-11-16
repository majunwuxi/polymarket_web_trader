"""足球赛果绑定汇总的单元与隔离 PostgreSQL 验证。"""
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import text

from test_z_auth_integration import pg, setup_scheduler_game  # noqa: F401
from test_zz_markets_integration import logged_client


def test_binding_summary_serialization(monkeypatch):
    from backend import result_binding_api as api
    now=datetime(2026,11,16,8,tzinfo=timezone.utc)
    game=SimpleNamespace(id=uuid.uuid4(),external_id='90108495',starts_at=now,status='scheduled')
    binding=SimpleNamespace(state='needs_review',provider='api_football',fixture_id=None,
                            candidates=[{'id':'123456','home':'Switzerland','away':'Scotland',
                                         'starts_at':now.isoformat(),'league':'UEFA Nations League'}],
                            last_error_code=None,updated_at=now,next_attempt_at=now)
    home=SimpleNamespace(name_zh='瑞士',source_name='Switzerland')
    away=SimpleNamespace(name_zh='苏格兰',source_name='Scotland')
    league=SimpleNamespace(name_zh='欧国联',name='UEFA Nations League')
    class Rows:
        def __init__(self,items): self.items=items
        def all(self): return self.items
    class FakeSession:
        calls=0
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,statement):
            type(self).calls+=1
            if type(self).calls==1:return Rows([('needs_review',1),('bound',1)])
            return Rows([(binding,game,home,away,league)])
        def scalar(self,statement): return 1
    monkeypatch.setattr(api,'Session',lambda engine:FakeSession())
    monkeypatch.setattr(api,'db_now',lambda db:now)
    data=api.binding_summary(object(),'football',20)
    assert data['totals']=={'total':2,'bound':1,'pending':0,'needs_review':1,
                           'unbound':1,'ready_to_confirm':1}
    assert data['items'][0]['home']=='瑞士'
    assert data['items'][0]['can_quick_confirm'] is True
    assert data['has_more'] is False


def test_football_binding_summary_and_quick_confirm(pg):
    game_id,_=setup_scheduler_game(pg,'football')
    with pg.begin() as conn:
        conn.execute(text("UPDATE games SET provider='polymarket',external_id='90108495' WHERE id=:id"),{'id':game_id})
        conn.execute(text("""
            INSERT INTO result_binding_requests
                (id,game_id,provider,state,candidates,next_attempt_at,updated_at)
            VALUES
                (:id,:game_id,'api_football','needs_review',CAST(:candidates AS jsonb),now(),now())
        """), {'id':uuid.uuid4(), 'game_id':game_id, 'candidates': json.dumps([{
            'id':'123456','starts_at':'2026-11-17T03:45:00+00:00',
            'home':'Switzerland','away':'Scotland','league':'UEFA Nations League',
        }])})

    client,headers=logged_client()
    try:
        result=client.get('/api/v1/result-bindings/summary?sport=football')
        assert result.status_code==200,result.text
        data=result.json()['data']
        assert data['totals']=={'total':1,'bound':0,'pending':0,'needs_review':1,
                               'unbound':1,'ready_to_confirm':1}
        assert data['items'][0]['can_quick_confirm'] is True
        assert data['items'][0]['candidates'][0]['id']=='123456'

        confirmed=client.post(f'/api/v1/games/{game_id}/result-binding/confirm',
                              json={'fixture_id':'123456'},headers=headers)
        assert confirmed.status_code==200,confirmed.text
        after=client.get('/api/v1/result-bindings/summary?sport=football').json()['data']
        assert after['totals']['needs_review']==0 and after['totals']['bound']==1
        assert after['totals']['unbound']==0 and after['items']==[]

        with pg.begin() as conn:
            conn.execute(text("UPDATE games SET sport='nba' WHERE id=:id"),{'id':game_id})
        assert client.get('/api/v1/result-bindings/summary?sport=football').json()['data']['totals']['total']==0
        assert client.get('/api/v1/result-bindings/summary?sport=nba').json()['data']['totals']['total']==1
    finally:
        client.__exit__(None,None,None)
