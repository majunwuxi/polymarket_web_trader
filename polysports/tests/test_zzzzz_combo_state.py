"""组合状态机集成测试，使用模拟 SDK 与隔离 PostgreSQL。"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as S
import uuid
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_z_auth_integration import pg  # noqa: F401
from test_zzz_trading_integration import env  # noqa: F401
from backend import combo_service
from backend.combo_api import ComboBody
from backend.models import ComboRFQ, Job, MarketLink, MarketOutcome, PolyMarket
from backend.trading_gateway import TradingError


class FakeGateway:
    calls=0
    def __init__(self,*args):self.client=self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def ensure_funding(self,*args):pass
    def request_combo_quote(self,**kwargs):
        type(self).calls+=1
        return S(rfq_id='mock-rfq',quote=S(rfq_id='mock-rfq',direction='BUY',expires_at=int((datetime.now(timezone.utc)+timedelta(minutes=3)).timestamp()*1000),blended_price=Decimal('0.5'),maker_amount=Decimal('9'),taker_amount=Decimal('18'),total_required=Decimal('10'),net_receive=None))


@pytest.fixture
def sample(env):
    engine,client,headers,body=env
    with Session(engine) as db,db.begin():
        link=db.get(MarketLink,uuid.UUID(body['market_link_id']));market=db.get(PolyMarket,link.market_id);outcome=db.get(MarketOutcome,uuid.UUID(body['outcome_id']))
        second=PolyMarket(id=uuid.uuid4(),gamma_market_id='999999999',poly_event_id=market.poly_event_id,condition_id='0x'+'f'*64,protocol_version=market.protocol_version,question='模拟第二市场',accepting_orders=True,fingerprint=market.fingerprint,last_verified_at=market.last_verified_at)
        db.add(second);db.flush()
        option=MarketOutcome(id=uuid.uuid4(),market_id=second.id,outcome_index=0,label='模拟第二选项',asset_id='987654321',asset_kind=outcome.asset_kind)
        other=MarketLink(id=uuid.uuid4(),game_id=link.game_id,market_id=second.id,state='confirmed',match_method='manual',evidence=link.evidence)
        db.add_all([option,other]);db.flush()
        payload=ComboBody(wallet_id=body['wallet_id'],direction='BUY',quantity='10',legs=[{'market_link_id':body['market_link_id'],'outcome_id':body['outcome_id']},{'market_link_id':other.id,'outcome_id':option.id}])
    FakeGateway.calls=0
    yield engine,client,headers,payload
    combo_service._quotes.clear()
    with engine.begin() as c:
        c.execute(text("DELETE FROM job_runs WHERE job_id IN (SELECT id FROM jobs WHERE combo_rfq_id IS NOT NULL)"))
        c.execute(text('DELETE FROM jobs WHERE combo_rfq_id IS NOT NULL'))
        for table in ('combo_events','combo_legs','combo_rfqs'):c.execute(text(f'DELETE FROM {table}'))


def catalog(markets,outcomes):return {x.id:str(i+1000) for i,x in enumerate(outcomes)}


def test_quote_idempotency_and_accept_once(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings;key=uuid.uuid4()
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,created=combo_service.request_quote(engine,settings,body,key,gateway_cls=FakeGateway,catalog=catalog)
    assert created and FakeGateway.calls==1
    assert combo_service.request_quote(engine,settings,body,key,gateway_cls=FakeGateway,catalog=catalog)==(ident,False)
    with pytest.raises(TradingError):combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:
        row=db.get(ComboRFQ,ident);qid=row.quote_id
        assert row.state=='quoted' and db.scalar(select(Job).where(Job.combo_rfq_id==ident))
    calls=[]
    monkeypatch.setattr(combo_service,'accept_once',lambda client,quote:(calls.append(1) or S(status='executing',rfq_id='mock-rfq')))
    combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    assert len(calls)==1
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='executing'


def test_timeout_stays_unknown_and_cannot_bypass_by_new_key(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    def fail(self,**kwargs):raise TimeoutError('模拟上游超时')
    monkeypatch.setattr(FakeGateway,'request_combo_quote',fail)
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='unknown'
    with pytest.raises(TradingError):combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)


def test_concurrent_accept_posts_once(sample,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:qid=db.get(ComboRFQ,ident).quote_id
    count=[];lock=threading.Lock()
    def accept(*args):
        with lock:count.append(1)
        return S(status='executing',rfq_id='mock-rfq')
    monkeypatch.setattr(combo_service,'accept_once',accept)
    def run():
        try:combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
        except TradingError:pass
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:run(),range(2)))
    assert len(count)==1


def test_lost_quote_object_cannot_be_accepted(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:qid=db.get(ComboRFQ,ident).quote_id
    combo_service._quotes.clear()
    with pytest.raises(TradingError) as error:combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    assert error.value.code=='quote_unavailable'
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='quoted'


def test_worker_recovers_known_rfq_without_resending(sample,monkeypatch):
    from backend.scheduler import claim_next
    from backend.worker import apply_work
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with engine.begin() as c:
        c.execute(text("UPDATE combo_rfqs SET state='accepting' WHERE id=:id"),{'id':ident})
        c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '100 years' WHERE combo_rfq_id=:id"),{'id':ident})
    job,token=claim_next(engine,'mock-worker')
    assert apply_work(engine,job,token,S(rfq_id='mock-rfq',status='EXECUTING',tx_hash=None))
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='executing'
    assert FakeGateway.calls==1


def test_polymarket_combo_binds_only_after_confirmed(sample,monkeypatch):
    from backend.models import ResultBindingRequest
    from backend.scheduler import claim_next
    from backend.worker import apply_work
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    with engine.begin() as conn:
        game_id=conn.execute(text('SELECT game_id FROM market_links WHERE id=:id'),{'id':body.legs[0].market_link_id}).scalar_one()
        conn.execute(text("UPDATE games SET provider='polymarket',external_id='1234567' WHERE id=:id"),{'id':game_id})
        conn.execute(text("UPDATE market_links SET evidence=evidence || jsonb_build_object('source_game_id','1234567') WHERE game_id=:id"),{'id':game_id})
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db: qid=db.get(ComboRFQ,ident).quote_id
    monkeypatch.setattr(combo_service,'accept_once',lambda *args:S(status='executing',rfq_id='mock-rfq'))
    combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:
        assert db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game_id)) is None
    with engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 day' WHERE combo_rfq_id=:id"),{'id':ident})
    job,token=claim_next(engine,'mock-worker')
    assert apply_work(engine,job,token,S(rfq_id='mock-rfq',status='CONFIRMED',tx_hash='mock-tx'))
    with Session(engine) as db:
        assert db.get(ComboRFQ,ident).state=='confirmed'
        request=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game_id))
        assert request and request.state=='pending' and request.provider=='balldontlie'


def test_no_quote_is_terminal_and_retry_permitted(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    monkeypatch.setattr(FakeGateway,'request_combo_quote',lambda self,**kw:S(rfq_id='mock-empty',quote=None))
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='failed'
    combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)


def test_last_look_rejection_is_not_a_fill(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:qid=db.get(ComboRFQ,ident).quote_id
    monkeypatch.setattr(combo_service,'accept_once',lambda *args:S(status='failed',rfq_id='mock-rfq'))
    combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='rejected'


def test_expired_quote_is_not_accepted(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:qid=db.get(ComboRFQ,ident).quote_id
    combo_service._quotes[ident][1].expires_at=0
    calls=[];monkeypatch.setattr(combo_service,'accept_once',lambda *args:calls.append(1))
    with pytest.raises(TradingError):combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    assert not calls


def test_only_one_combo_api_process_may_start(pg,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.config import get_settings
    from backend.main import create_app
    monkeypatch.setenv('POLYSPORTS_COMBO_EXECUTION_ENABLED','true')
    get_settings.cache_clear()
    with TestClient(create_app(),base_url='https://example.test'):
        with pytest.raises(RuntimeError,match='单个 API 进程'):
            with TestClient(create_app(),base_url='https://example.test'):
                pass
    get_settings.cache_clear()


def test_explicit_rfq_rejection_is_not_unknown(sample,monkeypatch):
    from polymarket import RfqRequestRejectedError
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    def reject(self,**kwargs):raise RfqRequestRejectedError('模拟拒绝',status=400,code='CONTRADICTORY_LEGS')
    monkeypatch.setattr(FakeGateway,'request_combo_quote',reject)
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:assert db.get(ComboRFQ,ident).state=='rejected'


def test_authenticated_combo_api_and_accept_flow(sample,monkeypatch):
    from backend import combo_api
    engine,client,headers,body=sample
    monkeypatch.setattr(client.app.state.settings,'combo_execution_enabled',True)
    monkeypatch.setattr(combo_api,'COMBO_EXECUTION_READY',True)
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    monkeypatch.setattr(combo_api,'request_quote',lambda e,s,b,k:combo_service.request_quote(e,s,b,k,gateway_cls=FakeGateway,catalog=catalog))
    monkeypatch.setattr(combo_api,'accept_quote',lambda e,s,i,q:combo_service.accept_quote(e,s,i,q,gateway_cls=FakeGateway,catalog=catalog))
    monkeypatch.setattr(combo_service,'accept_once',lambda *args:S(status='executing',rfq_id='mock-rfq'))
    key=str(uuid.uuid4());request_headers={**headers,'Idempotency-Key':key}
    result=client.post('/api/v1/combo-rfqs',json=body.model_dump(mode='json'),headers=request_headers)
    assert result.status_code==201,result.text
    data=result.json()['data'];assert data['state']=='quoted' and len(data['legs'])==2
    lookup=client.get('/api/v1/combo-rfqs/by-key/'+key+'?wallet_id='+str(body.wallet_id))
    assert lookup.status_code==200 and lookup.json()['data']['id']==data['id']
    accepted=client.post('/api/v1/combo-rfqs/'+data['id']+'/accept',json={'quote_id':data['quote']['id']},headers=headers)
    assert accepted.status_code==202 and accepted.json()['data']['state']=='executing'
    assert client.get('/api/v1/combo-rfqs/'+data['id']+'/events').status_code==200


def test_old_expiry_timer_cannot_delete_newer_quote():
    from backend.combo_service import _quotes, expire_cached_quote
    ident=uuid.uuid4();old=uuid.uuid4();new=uuid.uuid4()
    _quotes[ident]=(new,object(),b'mock')
    expire_cached_quote(ident,old)
    assert _quotes[ident][0]==new
    expire_cached_quote(ident,new)
    assert ident not in _quotes
