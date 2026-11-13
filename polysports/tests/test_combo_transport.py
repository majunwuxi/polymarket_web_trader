"""SDK 回归：使用模拟传输，绝不发送真实 RFQ。"""
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from polymarket import TransportError
from polymarket._internal.actions import combo_rfq

from backend.combo_transport import AcceptanceAlreadySent, accept_once


@dataclass(frozen=True)
class FakeContext:
    builder_gateway: object
    wallet: str = '0x'+'1'*40
    wallet_type: str = 'DEPOSIT_WALLET'


class BrokenTransport:
    def __init__(self):
        self.calls = 0

    def post_json(self, path, **kwargs):
        self.calls += 1
        raise TransportError('模拟网络中断，可能已发送')



def mock_quote():
    return SimpleNamespace(rfq_id='mock-rfq-1',quote_id='mock-quote',maker_amount='10',taker_amount='20',direction='BUY',position_id='123',builder_code='0x'+'2'*64)


def signed_body():
    wallet='0x'+'1'*40
    return {'quote_id':'mock-quote','signed_order':{'maker':wallet,'signer':wallet,'tokenId':'123','makerAmount':'10000000','takerAmount':'20000000','side':0,'signatureType':3,'builder':'0x'+'2'*64,'metadata':'0x'+'0'*64,'signature':'0xabc'}}

def test_sdk_internal_retry_never_reaches_transport(monkeypatch):
    # SDK 的签名和身份检查在此替换为模拟；保留 SDK 实际接受和重试流程。
    monkeypatch.setattr(combo_rfq, 'assert_combos_supported', lambda ctx: None)
    monkeypatch.setattr(combo_rfq, '_parse_combo_quote_input', lambda quote: quote)
    monkeypatch.setattr(combo_rfq, '_require_builder_api_key', lambda ctx: None)
    monkeypatch.setattr(combo_rfq, '_build_accept_request_body', lambda ctx, quote: signed_body())
    transport = BrokenTransport()
    client = SimpleNamespace(_ctx=FakeContext(builder_gateway=transport))
    quote = mock_quote()
    with pytest.raises(AcceptanceAlreadySent):
        accept_once(client, quote)
    assert transport.calls == 1


def test_unrelated_post_is_denied():
    from backend.combo_transport import _OneAcceptanceTransport
    transport = BrokenTransport()
    guarded = _OneAcceptanceTransport(transport, '/expected')
    with pytest.raises(RuntimeError):
        guarded.post_json('/unexpected')
    assert transport.calls == 0


def test_tampered_signed_amount_never_reaches_transport(monkeypatch):
    monkeypatch.setattr(combo_rfq,'assert_combos_supported',lambda ctx:None)
    monkeypatch.setattr(combo_rfq,'_parse_combo_quote_input',lambda quote:quote)
    monkeypatch.setattr(combo_rfq,'_require_builder_api_key',lambda ctx:None)
    bad=signed_body();bad['signed_order']['makerAmount']='10000001'
    monkeypatch.setattr(combo_rfq,'_build_accept_request_body',lambda ctx,quote:bad)
    transport=BrokenTransport();client=SimpleNamespace(_ctx=FakeContext(builder_gateway=transport))
    with pytest.raises(ValueError,match='不一致'):accept_once(client,mock_quote())
    assert transport.calls==0


def test_real_sdk_builds_and_signs_expected_deposit_wallet_order(monkeypatch):
    from decimal import Decimal
    from eth_account import Account
    from polymarket.environments import PRODUCTION
    # 固定公开测试私钥，仅用于离线测试签名。
    signer=Account.from_key('0x'+'22'*32)
    @dataclass(frozen=True)
    class SigningContext:
        builder_gateway: object
        wallet: str
        wallet_type: str
        signer: object
        environment_config: object
    monkeypatch.setattr(combo_rfq,'assert_combos_supported',lambda ctx:None)
    monkeypatch.setattr(combo_rfq,'_parse_combo_quote_input',lambda quote:quote)
    monkeypatch.setattr(combo_rfq,'_require_builder_api_key',lambda ctx:None)
    transport=BrokenTransport()
    ctx=SigningContext(transport,'0x'+'1'*40,'DEPOSIT_WALLET',signer,PRODUCTION._config)
    q=mock_quote();q.maker_amount=Decimal('10');q.taker_amount=Decimal('20');q.expires_at=9999999999999
    with pytest.raises(AcceptanceAlreadySent):accept_once(SimpleNamespace(_ctx=ctx),q)
    assert transport.calls==1
