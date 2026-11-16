"""赎回资格和网关单元测试；全部使用模拟响应。"""
import uuid
from types import SimpleNamespace

import httpx
import pytest

from backend.redeem import (
    fetch_redeemable_combo_positions,
    fetch_redeemable_single_positions,
)

from backend.redeem_gateway import RedeemGateway, RedeemUnknown
from backend.trading_gateway import TradingError


def test_single_positions_are_paginated_and_only_redeemable_winners_are_kept():
    calls = []
    def handler(request):
        calls.append(request)
        page = 1 if request.url.params.get('cursor') is None else 2
        rows = [{'token_id': '111', 'current_size': '3.5', 'redeemable': True,
                 'condition_id': '0x' + 'a' * 64, 'outcome_index': 0, 'negative_risk': False}]
        if page == 2:
            rows = [{'token_id': '222', 'current_size': '0', 'redeemable': True,
                     'condition_id': '0x' + 'a' * 64, 'outcome_index': 1, 'negative_risk': False},
                    {'token_id': '333', 'current_size': '2', 'redeemable': False,
                     'condition_id': '0x' + 'a' * 64, 'outcome_index': 1, 'negative_risk': False}]
        return httpx.Response(200, json={'data': rows, 'pagination': {
            'has_more': page == 1, 'next_cursor': 'next' if page == 1 else None}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        positions = fetch_redeemable_single_positions(client, '0x' + 'b' * 40, ['0x' + 'A' * 64])
    assert list(positions) == ['111']
    assert str(positions['111']['shares']) == '3.5'
    assert positions['111']['negative_risk'] is False
    assert calls[0].url.params['status'] == 'REDEEMABLE'
    assert calls[0].url.params['condition'] == '0x' + 'a' * 64
    assert calls[1].url.params['cursor'] == 'next'


def test_combo_positions_use_combo_position_id_and_skip_zero_balance():
    def handler(request):
        assert request.url.params['status'] == 'REDEEMABLE'
        return httpx.Response(200, json={'data': [
            {'combo_position_id': '9001', 'current_size': '4', 'redeemable': True},
            {'combo_position_id': '9002', 'current_size': '0', 'redeemable': True},
        ], 'pagination': {'has_more': False, 'next_cursor': None}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        positions = fetch_redeemable_combo_positions(client, '0x' + 'b' * 40)
    assert list(positions) == ['9001']
    assert str(positions['9001']['shares']) == '4'


def test_repeated_cursor_is_rejected():
    def handler(request):
        return httpx.Response(200, json={'data': [], 'pagination': {
            'has_more': True, 'next_cursor': 'same'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TradingError, match='分页重复'):
            fetch_redeemable_combo_positions(client, '0x' + 'b' * 40)


class Handle:
    def __init__(self, outcome=None, error=None):
        self.transaction_hash = '0x' + '1' * 64
        self.transaction_id = 'relayer-id'
        self.outcome = outcome
        self.error = error
    def wait(self):
        if self.error:
            raise self.error
        return self.outcome


class FakeClient:
    def __init__(self, handle):
        self.handle = handle
        self.arguments = None
    def redeem_positions(self, **kwargs):
        self.arguments = kwargs
        return self.handle


def test_gateway_redeems_by_condition_or_position():
    gateway = object.__new__(RedeemGateway)
    gateway.client = FakeClient(Handle(SimpleNamespace(transaction_hash='0x' + 'a' * 64, transaction_id='tx-1')))
    tx_hash, transaction_id = gateway.redeem(condition_id='0x' + 'c' * 64)
    assert gateway.client.arguments == {'condition_id': '0x' + 'c' * 64}
    assert tx_hash == '0x' + 'a' * 64 and transaction_id == 'tx-1'

    gateway.client = FakeClient(Handle(SimpleNamespace(transaction_hash='0x' + 'b' * 64, transaction_id=None)))
    tx_hash, transaction_id = gateway.redeem(position_id='9001')
    assert gateway.client.arguments == {'position_id': '9001'}
    assert tx_hash == '0x' + 'b' * 64 and transaction_id == 'relayer-id'


def test_gateway_preserves_unknown_transaction_identity():
    gateway = object.__new__(RedeemGateway)
    gateway.client = FakeClient(Handle(error=TimeoutError('mock timeout')))
    with pytest.raises(RedeemUnknown) as exc:
        gateway.redeem(position_id='9001')
    assert exc.value.tx_hash == '0x' + '1' * 64
    assert exc.value.transaction_id == 'relayer-id'


def test_gateway_reports_sdk_call_exception_as_unknown():
    class Broken:
        def redeem_positions(self, **kwargs):
            raise RuntimeError('mock transport')
    gateway = object.__new__(RedeemGateway)
    gateway.client = Broken()
    with pytest.raises(RedeemUnknown):
        gateway.redeem(condition_id='0x' + 'c' * 64)


def test_gateway_marks_pre_send_input_error_as_rejected():
    from polymarket import UserInputError
    class BrokenInput:
        def redeem_positions(self, **kwargs):
            raise UserInputError('no balance')
    gateway = object.__new__(RedeemGateway)
    gateway.client = BrokenInput()
    with pytest.raises(TradingError, match='发送前'):
        gateway.redeem(position_id='9001')


def test_relayer_credentials_are_all_or_none_and_redacted():
    from pydantic import ValidationError, SecretStr
    from backend.config import WalletSecrets
    with pytest.raises(ValidationError):
        WalletSecrets(private_key='mock-private', relayer_api_key='mock-key')
    credential = WalletSecrets(private_key='mock-private', relayer_api_key='mock-key',
                               relayer_api_key_address='0x' + '3' * 40)
    assert 'mock-key' not in repr(credential)
    assert credential.relayer_api_key.get_secret_value() == 'mock-key'


def test_smart_wallet_can_use_relayer_api_key():
    from polymarket import RelayerApiKey
    from pydantic import SecretStr
    gateway = object.__new__(RedeemGateway)
    gateway.wallet = SimpleNamespace(wallet_type='DEPOSIT_WALLET')
    credential = SimpleNamespace(
        builder_api_key=None, builder_api_secret=None, builder_api_passphrase=None,
        relayer_api_key=SecretStr('mock-relayer-key'),
        relayer_api_key_address='0x' + '2' * 40,
    )
    key = gateway.sdk_api_key(credential)
    assert isinstance(key, RelayerApiKey)
    assert key.key == 'mock-relayer-key'
    assert 'mock-relayer-key' not in repr(key)


def test_api_returns_rejected_error_envelope(monkeypatch):
    from backend import redeem_api
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            engine=object(), settings=SimpleNamespace(redeem_execution_enabled=True))),
        state=SimpleNamespace(admin_session={'admin_id': uuid.uuid4()},
                              request_id=str(uuid.uuid4())))
    monkeypatch.setattr(redeem_api, 'redeem_single',
                        lambda *_args, **_kwargs: ({'state': 'rejected',
                                                   'message': '赎回请求在发送前被拒绝'}, 409))
    response = redeem_api._execute(request, 'single', uuid.uuid4(), str(uuid.uuid4()))
    assert response.status_code == 409
    assert b'REDEEM_REJECTED' in response.body
