"""全部为模拟数据，使用 MockTransport，不访问网络或发送真实订单。"""
import base64
import hashlib
import hmac
import json
from decimal import Decimal
from types import SimpleNamespace as S

import httpx
import pytest

from backend.combo_requester import RequesterClient, RequesterTransport, REQUESTS_PATH, ZERO_BUILDER
from backend.combo_transport import AcceptanceAlreadySent, _OneAcceptanceTransport


def context():
    return S(credentials=S(key='mock-key', secret=base64.urlsafe_b64encode(b'mock-secret').decode(), passphrase='mock-pass'),
             signer=S(address='0x'+'1'*40), wallet='0x'+'2'*40, wallet_type='DEPOSIT_WALLET')


def test_l2_signs_exact_body_and_path(monkeypatch):
    monkeypatch.setattr('backend.combo_requester.time.time', lambda: 12345)
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'ok': True})
    transport=RequesterTransport(context(), transport=httpx.MockTransport(handler))
    assert transport.post_json(REQUESTS_PATH,json={'a': '中文'}) == {'ok': True}
    req=seen[0]
    message=b'12345POST'+REQUESTS_PATH.encode()+req.content
    expected=base64.urlsafe_b64encode(hmac.new(b'mock-secret',message,hashlib.sha256).digest()).decode()
    assert req.headers['POLY_SIGNATURE'] == expected
    assert req.headers['POLY_ADDRESS'] == context().signer.address
    transport.close()


def test_quote_uses_zero_builder_and_validates_response(monkeypatch):
    import backend.combo_requester as module
    monkeypatch.setattr(module.combo_rfq,'assert_combos_supported',lambda ctx:None)
    monkeypatch.setattr(module.combo_rfq,'build_combo_quote_request_body',lambda ctx,**kw:(None,{'mock':'body'}))
    monkeypatch.setattr(module.combo_rfq,'_parse_combo_quote_result',lambda result,request:result)
    client=RequesterClient(S(_ctx=context(),close=lambda:None),transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'rfq_id':'mock'})))
    assert client.request_combo_quote(leg_position_ids=['1','2'], direction='BUY', amount=1)['builder_code']==ZERO_BUILDER
    client.close()


def test_real_sdk_builds_and_parses_personal_quote():
    ctx=context();ctx.signer_type='PRIVATE_KEY'
    def handler(request):
        body=json.loads(request.content)
        assert request.url.path==REQUESTS_PATH
        assert body['maker_address']==ctx.wallet and body['signer_address']==ctx.wallet
        assert body['requested_size']=={'unit':'notional','value_e6':'2000000'}
        response={'rfq_id':'mock-rfq','status':'AWAITING_REQUESTER_ACCEPTANCE','expires_at':2000000000000,
                  'request':{'rfq_id':'mock-rfq','maker_address':ctx.wallet,'leg_position_ids':['123','456'],
                             'condition_id':'0x03'+'a'*60,'yes_position_id':'789','no_position_id':'790',
                             'direction':'BUY','side':'YES','requested_size':body['requested_size']},
                  'quote':{'quote_id':'mock-quote','blended_price_e6':'500000','maker_amount_e6':'1000000',
                           'taker_amount_e6':'2000000','total_required_e6':'1000000','net_receive_e6':'2000000'}}
        return httpx.Response(200,json=response)
    client=RequesterClient(S(_ctx=ctx,close=lambda:None),transport=httpx.MockTransport(handler))
    result=client.request_combo_quote(leg_position_ids=['123','456'],direction='BUY',amount=Decimal('2'))
    assert result.quote.builder_code==ZERO_BUILDER
    assert result.quote.position_id=='789'
    client.close()


def test_accept_sends_once_and_never_retries_timeout(monkeypatch):
    import backend.combo_requester as module
    calls=[]
    ctx=context()
    quote=S(rfq_id='mock-rfq',quote_id='mock-quote',builder_code=ZERO_BUILDER,direction='BUY',position_id='123',maker_amount=Decimal('1'),taker_amount=Decimal('2'))
    order={'maker':ctx.wallet,'signer':ctx.wallet,'tokenId':'123','makerAmount':'1000000','takerAmount':'2000000','side':0,'signatureType':3,'builder':ZERO_BUILDER,'metadata':ZERO_BUILDER,'signature':'0x1234'}
    monkeypatch.setattr(module.combo_rfq,'_build_accept_request_body',lambda ctx,quote:{'quote_id':'mock-quote','signed_order':order})
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout('mock timeout')
    client=RequesterClient(S(_ctx=ctx,close=lambda:None),transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout): client.accept_once(quote)
    assert len(calls)==1
    assert calls[0].url.path == REQUESTS_PATH+'/mock-rfq/accept'
    client.close()


def test_real_sdk_signs_deposit_wallet_order_locally():
    from eth_account import Account
    ctx=context()
    ctx.signer=Account.from_key('0x'+'01'*32)
    ctx.environment_config=S(chain_id=137,exchange_v3='0xe3333700cA9d93003F00f0F71f8515005F6c00Aa')
    quote=S(rfq_id='mock-rfq',quote_id='mock-quote',builder_code=ZERO_BUILDER,direction='BUY',position_id='123',maker_amount=Decimal('1'),taker_amount=Decimal('2'),expires_at=2000000000000)
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'rfq_id':'mock-rfq','status':'EXECUTING'})
    client=RequesterClient(S(_ctx=ctx,close=lambda:None),transport=httpx.MockTransport(handler))
    result=client.accept_once(quote)
    assert result.status=='executing' and len(calls)==1
    assert calls[0]['signed_order']['builder']==ZERO_BUILDER
    assert calls[0]['signed_order']['signature'].startswith('0x')
    client.close()


def test_one_acceptance_transport_blocks_duplicate():
    delegate=S(post_json=lambda *a,**kw: None)
    guard=_OneAcceptanceTransport(delegate,'/mock')
    guard.post_json('/mock')
    with pytest.raises(AcceptanceAlreadySent): guard.post_json('/mock')
