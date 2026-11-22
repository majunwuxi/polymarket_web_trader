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
from backend.models import ComboRFQ, ComboLeg, Job, MarketLink, MarketOutcome, PolyMarket
from backend.trading_gateway import TradingError


class FakeGateway:
    calls=0
    def __init__(self,*args):self.client=self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def ensure_funding(self,*args):pass
    def request_combo_quote(self,**kwargs):
        type(self).calls+=1
        return S(rfq_id='mock-rfq',quote=S(rfq_id='mock-rfq',position_id='123456',direction='BUY',expires_at=int((datetime.now(timezone.utc)+timedelta(minutes=3)).timestamp()*1000),blended_price=Decimal('0.5'),maker_amount=Decimal('9'),taker_amount=Decimal('18'),total_required=Decimal('10'),net_receive=None))


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
    # 数据库隔离由 pg 夹具在下一个测试的 setup 统一清库（TRUNCATE CASCADE）；
    # 这里只清理进程内报价缓存，避免已删除表行导致的外键清理顺序问题。
    combo_service._quotes.clear()


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
    calls=[];order_hash='0x'+'a'*64
    monkeypatch.setattr(combo_service,'accept_once',lambda client,quote:(calls.append(1) or S(status='executing',rfq_id='mock-rfq',taker_order_hash=order_hash)))
    combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    combo_service.accept_quote(engine,settings,ident,qid,gateway_cls=FakeGateway,catalog=catalog)
    assert len(calls)==1
    with Session(engine) as db:
        row=db.get(ComboRFQ,ident)
        assert row.state=='executing' and row.accept_order_hash==order_hash


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
    from backend.models import ResultBindingRequest, WatchedGame
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
        watch=db.scalar(select(WatchedGame).where(WatchedGame.game_id==game_id))
        assert watch and watch.state=='active'
        poll=db.scalar(select(Job).where(Job.watched_game_id==watch.id,Job.kind=='result_poll'))
        assert poll and poll.due_at==watch.first_due_at
        first_poll_at = watch.first_due_at
    response=client.get('/api/v1/combo-rfqs?state=confirmed',headers=headers)
    assert response.status_code==200
    rows=response.json()['data']['items']
    selected=next(row for row in rows if row['id']==str(ident))
    leg=next(leg for leg in selected['legs'] if leg['game_id']==str(game_id))
    assert leg['watch_state']=='active' and leg['binding_state']=='pending'
    assert datetime.fromisoformat(leg['first_poll_at']) == first_poll_at
    assert leg['next_poll_at'] is not None
    assert selected['fill_confirmed_at'] is not None


def test_combo_close_uses_original_legs_and_blocks_second_unresolved_request(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    original,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with engine.begin() as conn:
        conn.execute(text("UPDATE combo_rfqs SET state='confirmed',quote_receive=18 WHERE id=:id"),{'id':original})
    def sell_quote(self,**kwargs):
        assert kwargs['direction']=='SELL'
        return S(rfq_id='mock-sell-rfq',quote=S(rfq_id='mock-sell-rfq',position_id='123456',direction='SELL',expires_at=int((datetime.now(timezone.utc)+timedelta(minutes=3)).timestamp()*1000),blended_price=Decimal('0.4'),maker_amount=Decimal('5'),taker_amount=Decimal('2'),total_required=Decimal('5'),net_receive=Decimal('2')))
    monkeypatch.setattr(FakeGateway,'request_combo_quote',sell_quote)
    sell=body.model_copy(update={'direction':'SELL','quantity':'5','parent_combo_id':original})
    close,_=combo_service.request_quote(engine,settings,sell,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:
        row=db.get(ComboRFQ,close)
        assert row.parent_combo_id==original and row.combo_position_id=='123456' and row.state=='quoted'
    with pytest.raises(TradingError,match='未解决'):
        combo_service.request_quote(engine,settings,sell,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)


def test_combo_close_ignores_stale_mapping_but_opening_still_blocked(sample,monkeypatch):
    """赛事映射过期时组合平仓仍可询价；BUY 开仓仍必须确认映射。"""
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda s,w:b'mock-credential-fingerprint')
    original,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with engine.begin() as conn:
        conn.execute(text("UPDATE combo_rfqs SET state='confirmed',quote_receive=18 WHERE id=:id"),{'id':original})
    def sell_quote(self,**kwargs):
        return S(rfq_id='mock-sell-rfq',quote=S(rfq_id='mock-sell-rfq',position_id='123456',direction='SELL',expires_at=int((datetime.now(timezone.utc)+timedelta(minutes=3)).timestamp()*1000),blended_price=Decimal('0.4'),maker_amount=Decimal('5'),taker_amount=Decimal('2'),total_required=Decimal('5'),net_receive=Decimal('2')))
    monkeypatch.setattr(FakeGateway,'request_combo_quote',sell_quote)
    sell=body.model_copy(update={'direction':'SELL','quantity':'5','parent_combo_id':original})
    monkeypatch.setattr(combo_service,'valid_link',lambda *args,**kwargs:False)
    close,_=combo_service.request_quote(engine,settings,sell,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with Session(engine) as db:assert db.get(ComboRFQ,close).state=='quoted'
    with pytest.raises(TradingError,match='赛事映射'):
        combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)


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


def test_authenticated_combo_api_auto_accept_places_in_one_step(sample,monkeypatch):
    from backend import combo_api
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(settings,'combo_execution_enabled',True)
    monkeypatch.setattr(combo_api,'COMBO_EXECUTION_READY',True)
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda s,w:b'mock-credential-fingerprint')
    monkeypatch.setattr(combo_api,'request_quote',lambda e,s,b,k:combo_service.request_quote(e,s,b,k,gateway_cls=FakeGateway,catalog=catalog))
    monkeypatch.setattr(combo_api,'accept_quote',lambda e,s,i,q:combo_service.accept_quote(e,s,i,q,gateway_cls=FakeGateway,catalog=catalog))
    signed=[]
    monkeypatch.setattr(combo_service,'accept_once',lambda *args:(signed.append(1) or S(status='executing',rfq_id='mock-rfq')))
    payload=body.model_dump(mode='json');payload['auto_accept']=True
    request_headers={**headers,'Idempotency-Key':str(uuid.uuid4())}
    result=client.post('/api/v1/combo-rfqs',json=payload,headers=request_headers)
    assert result.status_code==202,result.text
    data=result.json()['data'];assert data['state']=='executing' and data['quote']['id']
    with Session(engine) as db:
        row=db.get(ComboRFQ,uuid.UUID(data['id']))
        assert row.state=='executing' and row.accept_started_at is not None
    replay=client.post('/api/v1/combo-rfqs',json=payload,headers=request_headers)
    assert replay.status_code==200 and replay.json()['data']['state']=='executing'
    assert len(signed)==1  # 幂等重放不得再次签名下单


def test_auto_accept_failure_closes_quote_without_sending(sample,monkeypatch):
    from backend import combo_api
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(settings,'combo_execution_enabled',True)
    monkeypatch.setattr(combo_api,'COMBO_EXECUTION_READY',True)
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda s,w:b'mock-credential-fingerprint')
    monkeypatch.setattr(combo_api,'request_quote',lambda e,s,b,k:combo_service.request_quote(e,s,b,k,gateway_cls=FakeGateway,catalog=catalog))
    def fail(*args):raise TradingError('allowance_insufficient','组合交易授权不足')
    monkeypatch.setattr(combo_api,'accept_quote',fail)
    sent=[]
    monkeypatch.setattr(combo_service,'accept_once',lambda *args:(sent.append(1) or S(status='executing',rfq_id='mock-rfq')))
    payload=body.model_dump(mode='json');payload['auto_accept']=True
    result=client.post('/api/v1/combo-rfqs',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==503,result.text
    assert result.json()['error']['code']=='ALLOWANCE_INSUFFICIENT'
    assert not sent
    with Session(engine) as db:
        row=db.scalar(select(ComboRFQ).order_by(ComboRFQ.created_at.desc()).limit(1))
        assert row.state=='failed' and row.error_code=='allowance_insufficient'
        ident=row.id
    assert ident not in combo_service._quotes


def test_auto_accept_records_upstream_reason_and_voids_pending_fill(sample,monkeypatch):
    from backend import combo_api
    from backend.models import ComboFill
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(settings,'combo_execution_enabled',True)
    monkeypatch.setattr(combo_api,'COMBO_EXECUTION_READY',True)
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda s,w:b'mock-credential-fingerprint')
    monkeypatch.setattr(combo_api,'request_quote',lambda e,s,b,k:combo_service.request_quote(e,s,b,k,gateway_cls=FakeGateway,catalog=catalog))
    monkeypatch.setattr(combo_api,'accept_quote',lambda e,s,i,q:combo_service.accept_quote(e,s,i,q,gateway_cls=FakeGateway,catalog=catalog))
    monkeypatch.setattr(combo_service,'accept_once',lambda *args:S(status='failed',rfq_id='mock-rfq',reason='ACCEPTANCE_WINDOW_EXPIRED',error=S(code='EXPIRED_RFQ')))
    payload=body.model_dump(mode='json');payload['auto_accept']=True
    result=client.post('/api/v1/combo-rfqs',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==201,result.text
    data=result.json()['data']
    assert data['state']=='rejected' and data['error_code']=='expired_rfq'
    assert data['error_text'] and '过期' in data['error_text']
    with Session(engine) as db:
        fill=db.scalar(select(ComboFill).where(ComboFill.rfq_id==uuid.UUID(data['id'])))
        assert fill is not None and fill.state=='void'


def test_old_expiry_timer_cannot_delete_newer_quote():
    from backend.combo_service import _quotes, expire_cached_quote
    ident=uuid.uuid4();old=uuid.uuid4();new=uuid.uuid4()
    _quotes[ident]=(new,object(),b'mock')
    expire_cached_quote(ident,old)
    assert _quotes[ident][0]==new
    expire_cached_quote(ident,new)
    assert ident not in _quotes


def test_admin_can_delete_unresolved_combo_with_children(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with engine.begin() as conn:
        conn.execute(text("UPDATE combo_rfqs SET state='unknown',error_code='rfq_not_found' WHERE id=:id"),{'id':ident})
    response=client.delete(f'/api/v1/combo-rfqs/{ident}',headers=headers)
    assert response.status_code==200,response.text
    with Session(engine) as db:
        assert db.get(ComboRFQ,ident) is None
        assert db.scalar(select(ComboLeg).where(ComboLeg.rfq_id==ident)) is None
        assert db.scalar(select(Job).where(Job.combo_rfq_id==ident)) is None


def test_unresolved_or_confirmed_combo_cannot_be_deleted(sample,monkeypatch):
    engine,client,headers,body=sample;settings=client.app.state.settings
    monkeypatch.setattr(combo_service,'credential_fingerprint',lambda settings,wallet:b'mock-credential-fingerprint')
    ident,_=combo_service.request_quote(engine,settings,body,uuid.uuid4(),gateway_cls=FakeGateway,catalog=catalog)
    with engine.begin() as conn:
        conn.execute(text("UPDATE combo_rfqs SET state='executing' WHERE id=:id"),{'id':ident})
    response=client.delete(f'/api/v1/combo-rfqs/{ident}',headers=headers)
    assert response.status_code==409
    assert response.json()['error']['code']=='COMBO_NOT_DELETABLE'
    with Session(engine) as db:
        assert db.get(ComboRFQ,ident) is not None


def test_watchdog_marks_manually_closed_combo_and_hides_it_from_records(env,monkeypatch):
    from backend import combo_reconcile
    engine,client,headers,body=env
    link_id=uuid.UUID(body['market_link_id']);outcome_id=uuid.UUID(body['outcome_id']);wallet_id=uuid.UUID(body['wallet_id'])
    rfq_id=uuid.uuid4()
    with Session(engine) as db,db.begin():
        link=db.get(MarketLink,link_id);market=db.get(PolyMarket,link.market_id);market.accepting_orders=True
        db.add(ComboRFQ(id=rfq_id,wallet_id=wallet_id,wallet_address='0x'+'1'*40,client_request_id=uuid.uuid4(),
                        fingerprint=uuid.uuid4().hex,direction='BUY',quantity=Decimal('10'),state='confirmed',
                        external_rfq_id='mock-rfq-'+uuid.uuid4().hex,combo_position_id='123456'))
        db.flush()
        db.add(ComboLeg(id=uuid.uuid4(),rfq_id=rfq_id,game_id=link.game_id,market_link_id=link_id,
                        outcome_id=outcome_id,position_id='1001'))
    listed=[item['id'] for item in client.get('/api/v1/combo-rfqs',params={'state':'confirmed'}).json()['data']['items']]
    assert str(rfq_id) in listed
    # 链上余额为 0 且赛事未结算：视为已在 Polymarket 手动平仓。
    from backend.scheduler import claim_next
    from backend.worker import apply_work, fetch_work

    def read_balance(wallet, position):
        assert engine.pool.checkedout() == 0
        return Decimal('0')

    monkeypatch.setattr(combo_reconcile, 'combo_balance_reader', lambda settings: read_balance)
    settings = S(polygon_rpc_url=object())
    watchdog_id = uuid.uuid4()
    with Session(engine) as db, db.begin():
        db.add(Job(id=watchdog_id, kind='combo_watchdog', dedupe_key='mock-watchdog',
                   due_at=datetime.now(timezone.utc) - timedelta(days=1), status='pending'))
    job_id, token = claim_next(engine, 'mock-worker')
    assert job_id == watchdog_id
    evidence = fetch_work(engine, job_id, token, settings, None)
    assert apply_work(engine, job_id, token, evidence, settings=settings)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT position_closed_at FROM combo_rfqs WHERE id=:id'),{'id':rfq_id}).scalar_one() is not None
    listed=[item['id'] for item in client.get('/api/v1/combo-rfqs',params={'state':'confirmed'}).json()['data']['items']]
    assert str(rfq_id) not in listed
    history=client.get('/api/v1/history/trades',params={'kind':'combo'}).json()['data']['items']
    assert all(row['id']!=str(rfq_id) for row in history)


def test_watchdog_keeps_combo_while_position_balance_positive(env,monkeypatch):
    from backend import combo_reconcile
    engine,client,headers,body=env
    link_id=uuid.UUID(body['market_link_id']);outcome_id=uuid.UUID(body['outcome_id']);wallet_id=uuid.UUID(body['wallet_id'])
    rfq_id=uuid.uuid4()
    with Session(engine) as db,db.begin():
        link=db.get(MarketLink,link_id);market=db.get(PolyMarket,link.market_id);market.accepting_orders=True
        db.add(ComboRFQ(id=rfq_id,wallet_id=wallet_id,wallet_address='0x'+'1'*40,client_request_id=uuid.uuid4(),
                        fingerprint=uuid.uuid4().hex,direction='BUY',quantity=Decimal('10'),state='confirmed',
                        external_rfq_id='mock-rfq-'+uuid.uuid4().hex,combo_position_id='123456'))
        db.flush()
        db.add(ComboLeg(id=uuid.uuid4(),rfq_id=rfq_id,game_id=link.game_id,market_link_id=link_id,
                        outcome_id=outcome_id,position_id='1001'))
    monkeypatch.setattr(combo_reconcile, 'combo_balance_reader', lambda settings: lambda *args: Decimal('5'))
    settings = S(polygon_rpc_url=object())
    evidence = combo_reconcile.fetch_combo_watchdog(engine, settings)
    with Session(engine) as db, db.begin():
        counts = combo_reconcile.apply_combo_watchdog(db, datetime.now(timezone.utc), evidence)
    assert counts['closed']==0
    with engine.connect() as conn:
        assert conn.execute(text('SELECT position_closed_at FROM combo_rfqs WHERE id=:id'),{'id':rfq_id}).scalar_one() is None
    listed=[item['id'] for item in client.get('/api/v1/combo-rfqs',params={'state':'confirmed'}).json()['data']['items']]
    assert str(rfq_id) in listed
