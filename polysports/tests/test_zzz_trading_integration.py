"""模拟交易所与隔离 PostgreSQL；不得发送真实订单。"""
import json,threading,uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
import pytest
from sqlalchemy import text
from polymarket import RequestRejectedError
from backend.config import get_settings
from backend import trading,trading_api
from backend.trading_gateway import TradingError,finite_decimal,validate_rules,restriction,NoDeployClient
from backend.worker import apply_work
from backend.scheduler import claim_next
from test_z_auth_integration import pg
from test_zz_markets_integration import prepare as prepare_game,refresh_mock,logged_client


class MockGateway:
    posts=0;signs=0;hash='mock-book';post_error=None;cancel_result=None;cancel_error=None
    mutex=threading.Lock()
    def __init__(self,*args):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def inspect(self,market,outcome,side,price,size,event):
        validate_rules(price,size,Decimal('0.01'),Decimal('5'))
        return {'hash':self.hash,'tick':'0.01','minimum':'5','balance':'100','allowance_status':'ready'}
    def sign(self,*args):
        with self.mutex: type(self).signs+=1
        return object()
    def post(self,signed):
        with self.mutex: type(self).posts+=1
        if self.post_error:raise self.post_error
        return SimpleNamespace(ok=True,order_id='0x'+'b'*64)
    def cancel(self,order):
        if self.cancel_error:raise self.cancel_error
        return self.cancel_result or SimpleNamespace(canceled=(order,),not_canceled={})


@pytest.fixture
def env(pg,monkeypatch):
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM job_runs WHERE job_id IN (SELECT id FROM jobs WHERE trade_intent_id IS NOT NULL)"))
        conn.execute(text('DELETE FROM jobs WHERE trade_intent_id IS NOT NULL'))
        for table in ('trade_fills','trade_state_events','trade_submissions','trade_intents'):conn.execute(text(f'DELETE FROM {table}'))
    game_id,event=prepare_game(pg);refresh_mock(pg,game_id,event)
    wallet={'label':'模拟钱包','wallet_address':'0x'+'1'*40,'signer_address':'0x'+'1'*40,'wallet_type':'EOA'}
    monkeypatch.setenv('POLYSPORTS_WALLETS',json.dumps([wallet]));get_settings.cache_clear()
    MockGateway.posts=MockGateway.signs=0;MockGateway.hash='mock-book';MockGateway.post_error=None;MockGateway.cancel_result=None;MockGateway.cancel_error=None
    monkeypatch.setattr(trading_api,'Gateway',MockGateway)
    monkeypatch.setattr(trading_api,'prepare',lambda e,s,b:trading.prepare(e,s,b,gateway_cls=MockGateway))
    monkeypatch.setattr(trading_api,'execute',lambda e,s,b,k:trading.execute(e,s,b,k,gateway_cls=MockGateway))
    client,headers=logged_client()
    mapping=client.get(f'/api/v1/games/{game_id}/markets').json()['data']['items'][0]
    assert client.post('/api/v1/market-links/'+mapping['link']['id']+'/confirm',json={},headers=headers).status_code==200
    wallet_id=client.get('/api/v1/wallets').json()['data']['items'][0]['id']
    body={'wallet_id':wallet_id,'market_link_id':mapping['link']['id'],'outcome_id':mapping['market']['outcomes'][0]['id'],'side':'BUY','limit_price':'0.50','size':'5.00'}
    yield pg,client,headers,body
    client.__exit__(None,None,None);get_settings.cache_clear()


def preview(client,headers,body):
    r=client.post('/api/v1/single-orders/preview',json=body,headers=headers)
    assert r.status_code==200,r.text
    return {**body,'preview_token':r.json()['data']['preview_token']}


def test_decimal_restrictions_and_no_implicit_deployment():
    validate_rules(Decimal('0.50'),Decimal('5'),Decimal('0.01'),Decimal('5'))
    for p,s in [('0.505','5'),('0.50','4'),('0.50','5.001'),('NaN','5')]:
        with pytest.raises(TradingError):validate_rules(Decimal(p),Decimal(s),Decimal('0.01'),Decimal('5'))
    with pytest.raises(TradingError):finite_decimal(0.5,'模拟价格')
    with pytest.raises(TradingError,match='未部署'):NoDeployClient._deploy_default_deposit_wallet(None)
    e=restriction(RequestRejectedError('mock',status=425,retry_after=12.5))
    assert e.code=='engine_restarting' and e.retry_after==13
    assert restriction(RequestRejectedError('mock',status=400,code='post_only_mode')).code=='post_only_mode'


def test_preview_and_submit_atomic_idempotent(env):
    pg,client,headers,body=env
    submit=preview(client,headers,body)
    with pg.connect() as c:assert c.execute(text('SELECT count(*) FROM trade_intents')).scalar_one()==0
    key=str(uuid.uuid4());h={**headers,'Idempotency-Key':key}
    result=client.post('/api/v1/single-orders',json=submit,headers=h)
    assert result.status_code==201,result.text
    data=result.json()['data']['intent'];assert data['state']=='pending' and data['matched_size'] is None
    assert MockGateway.posts==1
    again=client.post('/api/v1/single-orders',json=submit,headers=h)
    assert again.status_code==200 and again.json()['data']['intent']['id']==data['id'] and MockGateway.posts==1
    changed={**submit,'size':'6.00'}
    assert client.post('/api/v1/single-orders',json=changed,headers=h).status_code==409
    other=client.post('/api/v1/single-orders',json=submit,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert other.status_code==409 and MockGateway.posts==1
    with pg.connect() as c:
        assert c.execute(text('SELECT count(*) FROM watched_games')).scalar_one()==1
        assert c.execute(text("SELECT count(*) FROM jobs WHERE kind='trade_reconcile'")).scalar_one()==1
        assert c.execute(text('SELECT count(*) FROM trade_submissions')).scalar_one()==1


def test_unknown_submission_never_reposts_and_worker_restart(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body);MockGateway.post_error=TimeoutError('mock timeout')
    key=str(uuid.uuid4());h={**headers,'Idempotency-Key':key}
    result=client.post('/api/v1/single-orders',json=payload,headers=h)
    assert result.status_code==202 and result.json()['data']['intent']['state']=='unknown'
    assert client.post('/api/v1/single-orders',json=payload,headers=h).status_code==202
    assert MockGateway.posts==1
    with pg.begin() as c:c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE kind='trade_reconcile'"))
    job_id,token=claim_next(pg,'mock-restarted-worker')
    assert apply_work(pg,job_id,token,None)
    assert client.get('/api/v1/trade-intents/by-key/'+key,params={'wallet_id':body['wallet_id']}).json()['data']['state']=='unknown'
    assert MockGateway.posts==1


@pytest.mark.parametrize('result_state',['accepted','unknown','rejected'])
def test_polymarket_binding_waits_for_accepted_order(env,result_state,monkeypatch):
    pg,client,headers,body=env
    with pg.begin() as conn:
        game_id=conn.execute(text('SELECT game_id FROM market_links WHERE id=:id'),{'id':body['market_link_id']}).scalar_one()
        conn.execute(text("UPDATE games SET provider='polymarket',external_id='9876543' WHERE id=:id"),{'id':game_id})
        conn.execute(text("UPDATE market_links SET evidence=evidence || jsonb_build_object('source_game_id','9876543') WHERE id=:id"),{'id':body['market_link_id']})
    payload=preview(client,headers,body)
    if result_state=='unknown': MockGateway.post_error=TimeoutError('mock timeout')
    if result_state=='rejected': monkeypatch.setattr(MockGateway,'post',lambda self,signed:SimpleNamespace(ok=False,code='mock_rejected'))
    result=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==(202 if result_state=='unknown' else 201)
    with pg.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM result_binding_requests WHERE game_id=:id'),{'id':game_id}).scalar_one()==int(result_state=='accepted')
        assert conn.execute(text('SELECT count(*) FROM watched_games WHERE game_id=:id'),{'id':game_id}).scalar_one()==int(result_state=='accepted')


def test_preview_changed_book_and_expired_or_bad_token(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body);MockGateway.hash='mock-new-book'
    r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert r.status_code==409 and MockGateway.posts==0
    payload['preview_token']='invalid'
    assert client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())}).status_code==409
    with pg.connect() as c:assert c.execute(text('SELECT count(*) FROM trade_intents')).scalar_one()==0


def test_order_restriction_and_cancel_unknown(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body);MockGateway.post_error=RequestRejectedError('mock 425',status=425,retry_after=20)
    r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert r.json()['data']['intent']['state']=='rejected' and r.json()['data']['intent']['error_code']=='engine_restarting'
    assert r.json()['data']['intent']['retry_after_seconds']==20
    MockGateway.post_error=None
    p=preview(client,headers,{**body,'size':'6'})
    r=client.post('/api/v1/single-orders',json=p,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    ident=r.json()['data']['intent']['id'];MockGateway.cancel_error=TimeoutError('mock')
    r=client.post('/api/v1/single-orders/'+ident+'/cancel',json={},headers=headers)
    assert r.status_code==202 and r.json()['data']['intent']['cancel_state']=='unknown'
    assert client.post('/api/v1/single-orders/'+ident+'/cancel',json={},headers=headers).status_code==202


def test_concurrent_same_key_submits_once(env):
    pg,client,headers,body=env
    from backend.trading_api import SubmitBody
    payload=preview(client,headers,body);key=uuid.uuid4();settings=get_settings();barrier=threading.Barrier(2)
    class ConcurrentGateway(MockGateway):
        def inspect(self,*args):
            result=super().inspect(*args);barrier.wait(timeout=10);return result
        def post(self,signed):
            with MockGateway.mutex:MockGateway.posts+=1
            return SimpleNamespace(ok=True,order_id='0x'+'b'*64)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(trading.execute,pg,settings,SubmitBody(**payload),key,ConcurrentGateway) for _ in range(2)]
        values=[f.result(timeout=30) for f in futures]
    assert values[0][0]==values[1][0] and sum(x[1] for x in values)==1 and MockGateway.posts==1


def test_crash_before_post_stays_unknown_and_does_not_retry(env):
    pg,client,headers,body=env
    from backend.trading_api import SubmitBody
    payload=preview(client,headers,body);key=uuid.uuid4()
    class CrashingGateway(MockGateway):
        def sign(self,*args):raise SystemExit('模拟进程崩溃')
    with pytest.raises(SystemExit):trading.execute(pg,get_settings(),SubmitBody(**payload),key,CrashingGateway)
    with pg.begin() as c:c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE kind='trade_reconcile'"))
    job_id,token=claim_next(pg,'mock-restart')
    assert apply_work(pg,job_id,token,None)
    state=client.get('/api/v1/trade-intents/by-key/'+str(key),params={'wallet_id':body['wallet_id']}).json()['data']
    assert state['state']=='unknown' and MockGateway.posts==0
    assert client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(key)}).status_code==202
    assert MockGateway.posts==0


def test_partial_full_and_confirmed_cancel_from_real_evidence(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body);r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())});ident=r.json()['data']['intent']['id']
    with pg.connect() as c:
        row=c.execute(text('SELECT m.condition_id,o.asset_id,t.wallet_address FROM trade_intents t JOIN market_links l ON l.id=t.market_link_id JOIN poly_markets m ON m.id=l.market_id JOIN market_outcomes o ON o.id=t.outcome_id WHERE t.id=:id'),{'id':ident}).one()
    evidence=SimpleNamespace(id='0x'+'b'*64,asset_id=row.asset_id,condition_id=row.condition_id,side='BUY',maker_address=row.wallet_address,price=Decimal('.50'),original_size=Decimal('5'),size_matched=Decimal('2'),status='LIVE')
    with pg.begin() as c:c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE trade_intent_id=:id"),{'id':ident})
    job_id,token=claim_next(pg,'mock-worker');assert apply_work(pg,job_id,token,evidence)
    detail=client.get('/api/v1/trade-intents/'+ident).json()['data']['intent']
    assert detail['state']=='partially_filled' and detail['matched_size']=='2'
    cancel=client.post('/api/v1/single-orders/'+ident+'/cancel',json={},headers=headers)
    assert cancel.status_code==200 and cancel.json()['data']['intent']['cancel_state']=='confirmed'
    assert cancel.json()['data']['intent']['state']=='cancelled' and cancel.json()['data']['intent']['matched_size']=='2'
    with pg.begin() as c:c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE trade_intent_id=:id"),{'id':ident})
    job_id,token=claim_next(pg,'mock-worker');assert apply_work(pg,job_id,token,evidence)
    assert client.get('/api/v1/trade-intents/'+ident).json()['data']['intent']['state']=='cancelled'


def test_signed_order_exactness_and_deposit_signer():
    from backend.trading_gateway import Gateway
    wallet=SimpleNamespace(wallet_type='DEPOSIT_WALLET',wallet_address='0x'+'1'*40,signer_address='0x'+'2'*40)
    gateway=Gateway(None,wallet)
    signed=SimpleNamespace(token_id='123',side='BUY',maker=wallet.wallet_address,signer=wallet.wallet_address,signature_type=3,maker_amount=2500000,taker_amount=5000000,order_type='GTC',expiration=0,post_only=False)
    gateway.client=SimpleNamespace(create_limit_order=lambda **_:signed)
    outcome=SimpleNamespace(asset_id='123')
    assert gateway.sign(outcome,'BUY',Decimal('0.5'),Decimal('5')) is signed
    signed.maker_amount+=1
    with pytest.raises(TradingError,match='不一致'):gateway.sign(outcome,'BUY',Decimal('0.5'),Decimal('5'))


def test_unknown_candidates_require_explicit_verified_order_id(env,monkeypatch):
    from datetime import datetime,timezone
    pg,client,headers,body=env
    payload=preview(client,headers,body);MockGateway.post_error=TimeoutError('mock')
    r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    ident=r.json()['data']['intent']['id']
    with pg.connect() as c:
        record=c.execute(text('SELECT m.condition_id,o.asset_id,t.wallet_address FROM trade_intents t JOIN market_links l ON l.id=t.market_link_id JOIN poly_markets m ON m.id=l.market_id JOIN market_outcomes o ON o.id=t.outcome_id WHERE t.id=:id'),{'id':ident}).one()
    order_id='0x'+'a'*64
    order=SimpleNamespace(id=order_id,asset_id=record.asset_id,condition_id=record.condition_id,side='BUY',maker_address=record.wallet_address,price=Decimal('.5'),original_size=Decimal('5'),size_matched=Decimal('0'),status='LIVE',created_at=datetime.now(timezone.utc))
    monkeypatch.setattr(MockGateway,'candidate_orders',lambda self,asset:([order],False),raising=False)
    monkeypatch.setattr(MockGateway,'get_order',lambda self,oid:order,raising=False)
    data=client.get('/api/v1/trade-intents/'+ident+'/reconcile-candidates').json()['data']
    assert data['scope']=='open_orders_only' and data['items'][0]['order_id']==order_id
    assert client.get('/api/v1/trade-intents/'+ident).json()['data']['intent']['external_order_id'] is None
    assert client.post('/api/v1/trade-intents/'+ident+'/attach-order',json={'order_id':order_id,'acknowledge_uncertainty':False},headers=headers).status_code==422
    order.price=Decimal('.6')
    assert client.post('/api/v1/trade-intents/'+ident+'/attach-order',json={'order_id':order_id,'acknowledge_uncertainty':True},headers=headers).status_code==409
    order.price=Decimal('.5')
    r=client.post('/api/v1/trade-intents/'+ident+'/attach-order',json={'order_id':order_id,'acknowledge_uncertainty':True},headers=headers)
    assert r.status_code==202 and r.json()['data']['status']=='unknown' and MockGateway.posts==1
    job_id,token=claim_next(pg,'mock-worker');assert apply_work(pg,job_id,token,order)
    assert client.get('/api/v1/trade-intents/'+ident).json()['data']['intent']['state']=='pending'


def test_gateway_rejects_unknown_allowance_and_identity(monkeypatch):
    from datetime import datetime,timezone
    import backend.trading_gateway as module
    from backend.gamma import parse_market
    from test_gamma import mock_event,mock_market,MOCK_BIG
    when=datetime(2026,1,1,12,tzinfo=timezone.utc)
    raw=mock_market();parsed=parse_market(raw);event_raw=mock_event(market=raw)
    event=SimpleNamespace(gamma_event_id='10',sport='nba',home_name='Mock Home',away_name='Mock Away',starts_at=when)
    market=SimpleNamespace(gamma_market_id='100',condition_id=parsed.condition_id,protocol_version='v1',fingerprint=parsed.fingerprint)
    outcome=SimpleNamespace(asset_id=MOCK_BIG,asset_kind='ctf_token',outcome_index=0)
    book=SimpleNamespace(asset_id=MOCK_BIG,condition_id=parsed.condition_id,tick_size=Decimal('.01'),min_order_size=Decimal('5'),neg_risk=False,hash='mock-hash',bids=(),asks=())
    gateway=module.Gateway(None,SimpleNamespace())
    account=SimpleNamespace(balance=100000000,allowances={})
    gateway.client=SimpleNamespace(get_order_book=lambda **_:book,get_balance_allowance=lambda **_:account)
    monkeypatch.setattr(module.httpx,'get',lambda *a,**k:SimpleNamespace(status_code=200,json=lambda:event_raw))
    monkeypatch.setattr(module,'is_v2_position_id',lambda _:False)
    monkeypatch.setattr(module,'resolve_order_exchange_address',lambda *a,**k:'0x'+'1'*40)
    with pytest.raises(TradingError,match='授权'):gateway.inspect(market,outcome,'BUY',Decimal('.5'),Decimal('5'),event)
    account.allowances={'0x'+'1'*40:100000000}
    assert gateway.inspect(market,outcome,'BUY',Decimal('.5'),Decimal('5'),event)['allowance_status']=='ready'
    event._expected_source_game_id='123'
    event_raw['gameId']='456'
    with pytest.raises(TradingError,match='编号'):gateway.inspect(market,outcome,'BUY',Decimal('.5'),Decimal('5'),event)
    event_raw['gameId']='123'
    assert gateway.inspect(market,outcome,'BUY',Decimal('.5'),Decimal('5'),event)['allowance_status']=='ready'
    event_raw['teams'][0]['name']='Different Mock Home'
    with pytest.raises(TradingError,match='变更'):gateway.inspect(market,outcome,'BUY',Decimal('.5'),Decimal('5'),event)


def test_gateway_blocks_mismatched_signer_and_geography(monkeypatch):
    import backend.trading_gateway as module
    from backend.config import Settings,WalletConfig,WalletSecrets
    wallet=WalletConfig(label='模拟',wallet_address='0x'+'1'*40,signer_address='0x'+'1'*40,wallet_type='EOA',credential_ref='mock')
    settings=Settings(database_url='postgresql+psycopg://mock:mock@127.0.0.1/mock',auth_secret='x'*40,public_origin='https://example.test',wallet_credentials={'mock':WalletSecrets(private_key='0x'+'2'*64)})
    monkeypatch.setattr(module,'ensure_geography',lambda:None)
    with pytest.raises(TradingError,match='签名者'):module.Gateway(settings,wallet).__enter__()
    monkeypatch.setattr(module,'ensure_geography',lambda:(_ for _ in ()).throw(TradingError('geoblock_blocked','模拟限制')))
    with pytest.raises(TradingError,match='限制'):module.Gateway(settings,wallet).__enter__()


def test_expired_preview_and_insufficient_balance_cannot_send(env,monkeypatch):
    pg,client,headers,body=env
    payload=preview(client,headers,body)
    from backend.trading_api import SubmitBody
    parsed=SubmitBody(**payload);normalized,_,_=trading.normalize(parsed)
    payload['preview_token']=trading.encode_token(get_settings(),{'fingerprint':trading.fingerprint(normalized),'book_hash':'mock-book','expires_at':1})
    r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert r.status_code==409 and MockGateway.posts==0
    def no_balance(*args):raise TradingError('allowance_unknown','模拟授权未知')
    monkeypatch.setattr(MockGateway,'inspect',no_balance)
    r=client.post('/api/v1/single-orders/preview',json=body,headers=headers)
    assert r.status_code==503 and r.json()['error']['code']=='ALLOWANCE_UNKNOWN'
    with pg.connect() as conn:assert conn.execute(text('SELECT count(*) FROM trade_intents')).scalar_one()==0


def test_crash_after_transmission_marks_unknown_without_resend(env):
    pg,client,headers,body=env
    from backend.trading_api import SubmitBody
    payload=preview(client,headers,body);key=uuid.uuid4()
    class CrashAfterSend(MockGateway):
        def post(self,signed):
            with MockGateway.mutex:MockGateway.posts+=1
            raise SystemExit('模拟发送后进程崩溃')
    with pytest.raises(SystemExit):trading.execute(pg,get_settings(),SubmitBody(**payload),key,CrashAfterSend)
    with pg.begin() as conn:conn.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE kind='trade_reconcile'"))
    job_id,token=claim_next(pg,'mock-worker');assert apply_work(pg,job_id,token,None)
    assert client.get('/api/v1/trade-intents/by-key/'+str(key),params={'wallet_id':body['wallet_id']}).json()['data']['state']=='unknown'
    assert MockGateway.posts==1
    assert client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(key)}).status_code==202
    assert MockGateway.posts==1


def test_filled_requires_matching_order_evidence(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body);r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())});ident=r.json()['data']['intent']['id']
    with pg.connect() as c:
        row=c.execute(text('SELECT m.condition_id,o.asset_id,t.wallet_address FROM trade_intents t JOIN market_links l ON l.id=t.market_link_id JOIN poly_markets m ON m.id=l.market_id JOIN market_outcomes o ON o.id=t.outcome_id WHERE t.id=:id'),{'id':ident}).one()
    order=SimpleNamespace(id='0x'+'b'*64,asset_id=row.asset_id,condition_id=row.condition_id,side='BUY',maker_address=row.wallet_address,price=Decimal('.5'),original_size=Decimal('5'),size_matched=Decimal('5'),status='MATCHED')
    with pg.begin() as c:c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE trade_intent_id=:id"),{'id':ident})
    job_id,token=claim_next(pg,'mock-worker');assert apply_work(pg,job_id,token,order)
    result=client.get('/api/v1/trade-intents/'+ident).json()['data']['intent']
    assert result['state']=='filled' and result['matched_size']=='5'
    with pg.connect() as c:assert c.execute(text('SELECT status FROM jobs WHERE trade_intent_id=:id'),{'id':ident}).scalar_one()=='done'


def test_cancel_explicit_rejection_may_be_retried_but_unknown_may_not(env):
    pg,client,headers,body=env
    p=preview(client,headers,body);r=client.post('/api/v1/single-orders',json=p,headers={**headers,'Idempotency-Key':str(uuid.uuid4())});ident=r.json()['data']['intent']['id']
    MockGateway.cancel_result=SimpleNamespace(canceled=(),not_canceled={'0x'+'b'*64:'mock rejection'})
    r=client.post('/api/v1/single-orders/'+ident+'/cancel',json={},headers=headers)
    assert r.json()['data']['intent']['cancel_state']=='rejected'
    MockGateway.cancel_result=None
    r=client.post('/api/v1/single-orders/'+ident+'/cancel',json={},headers=headers)
    assert r.json()['data']['intent']['cancel_state']=='confirmed'


def test_undeployed_smart_wallet_never_initializes_sdk(monkeypatch):
    import backend.trading_gateway as module
    from backend.config import Settings,WalletConfig,WalletSecrets
    from eth_account import Account
    key='0x'+'2'*64;signer=Account.from_key(key).address.lower()
    wallet=WalletConfig(label='模拟',wallet_address='0x'+'1'*40,signer_address=signer,wallet_type='DEPOSIT_WALLET',credential_ref='mock')
    settings=Settings(database_url='postgresql+psycopg://mock:mock@127.0.0.1/mock',auth_secret='x'*40,public_origin='https://example.test',wallet_credentials={'mock':WalletSecrets(private_key=key)},polygon_rpc_url='https://mock.invalid')
    monkeypatch.setattr(module,'ensure_geography',lambda:None)
    monkeypatch.setattr(module.Web3,'HTTPProvider',lambda *a,**k:object())
    monkeypatch.setattr(module,'Web3',SimpleNamespace(HTTPProvider=lambda *a,**k:object(),to_checksum_address=lambda x:x))
    # 伪造链 RPC：无代码；不允许进入 SDK 初始化。
    fakeweb=SimpleNamespace(eth=SimpleNamespace(chain_id=137,get_code=lambda _:b''))
    class FakeWeb3:
        HTTPProvider=staticmethod(lambda *a,**k:object())
        to_checksum_address=staticmethod(lambda x:x)
        def __new__(cls,*a,**k):return fakeweb
    monkeypatch.setattr(module,'Web3',FakeWeb3)
    monkeypatch.setattr(module.NoDeployClient,'create',lambda **kwargs:pytest.fail('不应初始化 SDK'))
    with pytest.raises(TradingError,match='未部署'):module.Gateway(settings,wallet).__enter__()


def test_official_sdk_signed_order_matches_confirmed_parameters_without_network():
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from polymarket._internal.actions.orders.types import OrderDraft
    from polymarket._internal.actions.orders.orders import create_unsigned_order,create_signed_order
    from polymarket._internal.actions.orders.typed_data import build_order_typed_data
    from backend.trading_gateway import Gateway
    # 仅供测试的公开固定私钥，绝不用于真实账户。
    account=Account.from_key('0x'+'22'*32)
    draft=OrderDraft(chain_id=137,exchange_address='0xE111180000d2663C0091e4f400237545B87B996B',expiration=0,funder_address=account.address,offered_amount=2500000,order_type='GTC',side='BUY',signer=account.address,requested_amount=5000000,asset_id='123')
    unsigned=create_unsigned_order(draft,wallet=account.address,wallet_type='EOA')
    signable=encode_typed_data(full_message=build_order_typed_data(unsigned))
    signature=account.sign_message(signable).signature.hex()
    signed=create_signed_order(unsigned,'0x'+signature)
    wallet=SimpleNamespace(wallet_type='EOA',wallet_address=account.address.lower(),signer_address=account.address.lower())
    gateway=Gateway(None,wallet);gateway.client=SimpleNamespace(create_limit_order=lambda **_:signed)
    assert gateway.sign(SimpleNamespace(asset_id='123'),'BUY',Decimal('.5'),Decimal('5')) is signed
    assert Account.recover_message(signable,signature=signed.signature).lower()==wallet.signer_address


def test_fill_query_persists_verified_records_and_detects_conflict(env,monkeypatch):
    from datetime import datetime,timezone
    pg,client,headers,body=env
    payload=preview(client,headers,body);result=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    ident=result.json()['data']['intent']['id'];now=datetime.now(timezone.utc)
    sample={'trade_id':'mock-fill-1','size':Decimal('2'),'price':Decimal('.49'),'status':'MATCHED','matched_at':now}
    monkeypatch.setattr(MockGateway,'fills',lambda self,*args:([sample],False),raising=False)
    first=client.get('/api/v1/trade-intents/'+ident+'/fills');assert first.status_code==200
    assert first.json()['data']['items'][0]['size']=='2' and first.json()['data']['items'][0]['status']=='MATCHED'
    assert client.get('/api/v1/trade-intents/'+ident+'/fills').status_code==200
    with pg.connect() as conn:assert conn.execute(text('SELECT count(*) FROM trade_fills')).scalar_one()==1
    sample['size']=Decimal('3')
    assert client.get('/api/v1/trade-intents/'+ident+'/fills').status_code==409
    with pg.connect() as conn:assert conn.execute(text('SELECT size FROM trade_fills')).scalar_one()==Decimal('2')


def test_sdk_fills_filter_by_order_and_never_count_unrelated():
    from datetime import datetime,timezone
    from backend.trading_gateway import Gateway
    matching=SimpleNamespace(id='mock-trade',condition_id='0x'+'a'*64,taker_order_id='0x'+'b'*64,asset_id='123',side='BUY',size=Decimal('2'),price=Decimal('.5'),maker_orders=(),status='MATCHED',matched_at=datetime.now(timezone.utc))
    unrelated=SimpleNamespace(id='mock-other',condition_id='0x'+'a'*64,taker_order_id='0x'+'c'*64,asset_id='123',side='BUY',size=Decimal('2'),price=Decimal('.5'),maker_orders=(),status='CONFIRMED',matched_at=datetime.now(timezone.utc))
    gateway=Gateway(None,SimpleNamespace());gateway.client=SimpleNamespace(list_account_trades=lambda **kwargs:[SimpleNamespace(items=[matching,unrelated],has_more=False)])
    result,truncated=gateway.fills('123','0x'+'a'*64,'0x'+'b'*64,'BUY','0x'+'1'*40,Decimal('5'))
    assert len(result)==1 and result[0]['trade_id']=='mock-trade' and result[0]['status']=='MATCHED' and not truncated


def test_out_of_order_exchange_snapshot_cannot_reduce_confirmed_fills(env):
    pg,client,headers,body=env
    payload=preview(client,headers,body);r=client.post('/api/v1/single-orders',json=payload,headers={**headers,'Idempotency-Key':str(uuid.uuid4())});ident=r.json()['data']['intent']['id']
    with pg.connect() as c:row=c.execute(text('SELECT m.condition_id,o.asset_id,t.wallet_address FROM trade_intents t JOIN market_links l ON l.id=t.market_link_id JOIN poly_markets m ON m.id=l.market_id JOIN market_outcomes o ON o.id=t.outcome_id WHERE t.id=:id'),{'id':ident}).one()
    order=SimpleNamespace(id='0x'+'b'*64,asset_id=row.asset_id,condition_id=row.condition_id,side='BUY',maker_address=row.wallet_address,price=Decimal('.5'),original_size=Decimal('5'),size_matched=Decimal('4'),status='LIVE')
    for amount in (Decimal('4'),Decimal('2')):
        order.size_matched=amount
        with pg.begin() as c:c.execute(text("UPDATE jobs SET due_at=clock_timestamp()-interval '1 second' WHERE trade_intent_id=:id"),{'id':ident})
        job,token=claim_next(pg,'mock-worker');assert apply_work(pg,job,token,order)
    result=client.get('/api/v1/trade-intents/'+ident).json()['data']['intent']
    assert result['state']=='partially_filled' and result['matched_size']=='4'
