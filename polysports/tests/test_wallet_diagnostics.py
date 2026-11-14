"""钱包诊断均使用模拟数据，不访问真实钱包或上游服务。"""
from types import SimpleNamespace

import pytest

from backend import market_api, trading_gateway


@pytest.fixture
def mock_wallet_request():
    wallet = SimpleNamespace(label='模拟钱包', wallet_address='0x' + '1' * 40,
                             signer_address='0x' + '2' * 40, wallet_type='DEPOSIT_WALLET',
                             credential_ref='primary')
    settings = SimpleNamespace(wallets=[wallet], wallet_credentials={'primary': object()})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
                              state=SimpleNamespace(request_id='mock-request'))
    return request, wallet, market_api.wallet_id(wallet.wallet_address)


@pytest.mark.parametrize(('code', 'geography_verified', 'availability'), [
    ('geoblock_blocked', False, 'blocked'),
    ('geoblock_unknown', False, 'unknown'),
    ('signer_mismatch', True, 'allowed'),
    ('wallet_deployment_unknown', True, 'allowed'),
    ('rpc_wrong_chain', True, 'allowed'),
    ('wallet_undeployed', True, 'allowed'),
    ('sdk_unavailable', True, 'allowed'),
    ('balance_unavailable', True, 'allowed'),
])
def test_mock_errors_return_safe_diagnostics(monkeypatch, mock_wallet_request, code, geography_verified, availability):
    request, _, wallet_id = mock_wallet_request
    secret = '模拟秘密_https://example.invalid/secret'

    class MockGateway:
        def __init__(self, *_): self.geography_verified = geography_verified
        def __enter__(self): raise trading_gateway.TradingError(code, secret)
        def __exit__(self, *_): pass

    monkeypatch.setattr(trading_gateway, 'Gateway', MockGateway)
    result = market_api.wallet_status(request, wallet_id)['data']
    assert result['availability'] == availability
    assert result['diagnostic']['code'] == code
    assert secret not in str(result)
    assert result['collateral_balance'] is None


def test_mock_unexpected_error_never_exposes_exception(monkeypatch, mock_wallet_request):
    request, _, wallet_id = mock_wallet_request
    class MockGateway:
        geography_verified = True
        def __init__(self, *_): pass
        def __enter__(self): raise RuntimeError('模拟私钥和 https://rpc.example/secret')
        def __exit__(self, *_): pass
    monkeypatch.setattr(trading_gateway, 'Gateway', MockGateway)
    result = market_api.wallet_status(request, wallet_id)['data']
    assert result['availability'] == 'allowed'
    assert result['diagnostic']['code'] == 'status_unavailable'
    assert 'secret' not in str(result) and '模拟私钥' not in str(result)


def test_mock_missing_credentials_does_not_contact_upstream(monkeypatch, mock_wallet_request):
    request, wallet, wallet_id = mock_wallet_request
    wallet.credential_ref = 'missing'
    monkeypatch.setattr(trading_gateway, 'Gateway', lambda *_: pytest.fail('不可访问上游'))
    result = market_api.wallet_status(request, wallet_id)['data']
    assert result['diagnostic']['code'] == 'credentials_missing'
    assert result['availability'] == 'unknown'


def test_mock_success_has_no_diagnostic(monkeypatch, mock_wallet_request):
    request, _, wallet_id = mock_wallet_request
    class MockGateway:
        geography_verified = True
        def __init__(self, *_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def wallet_status(self): return {'collateral_balance': '12.5', 'allowance_status': 'unknown'}
    monkeypatch.setattr(trading_gateway, 'Gateway', MockGateway)
    result = market_api.wallet_status(request, wallet_id)['data']
    assert result['diagnostic'] is None and result['availability'] == 'allowed'
    assert result['collateral_balance'] == '12.5' and result['checked_at']
