"""Builder 配置均为模拟值，不访问外部服务。"""
from types import SimpleNamespace
import pytest
from pydantic import SecretStr, ValidationError
from backend.combo_gateway import ComboGateway
from backend.config import WalletSecrets
from backend.trading_gateway import TradingError


def test_builder_credentials_are_all_or_none_and_redacted():
    with pytest.raises(ValidationError):
        WalletSecrets(private_key='mock',builder_api_key='mock-key')
    secret=WalletSecrets(private_key='mock',builder_api_key='mock-key',builder_api_secret='mock-secret',builder_api_passphrase='mock-passphrase')
    assert 'mock-secret' not in repr(secret)
    gateway=ComboGateway(None,SimpleNamespace(wallet_type='DEPOSIT_WALLET'))
    assert gateway.sdk_api_key(secret).key == 'mock-key'


def test_builder_requires_smart_wallet_and_credentials():
    secret=WalletSecrets(private_key=SecretStr('mock'))
    with pytest.raises(TradingError) as error:
        ComboGateway(None,SimpleNamespace(wallet_type='EOA')).sdk_api_key(secret)
    assert error.value.code=='combo_wallet_unsupported'
    with pytest.raises(TradingError) as error:
        ComboGateway(None,SimpleNamespace(wallet_type='DEPOSIT_WALLET')).sdk_api_key(secret)
    assert error.value.code=='builder_credentials_missing'


def test_combo_funding_uses_required_amount_and_exchange_allowance(monkeypatch):
    from decimal import Decimal
    import web3
    class Call:
        def __init__(self,value):self.value=value
        def call(self):return self.value
    class Functions:
        def balanceOf(self,*args):return Call(9_000_000)
        def allowance(self,*args):return Call(20_000_000)
    class Web3Mock:
        def __init__(self,*args):self.eth=SimpleNamespace(chain_id=137,contract=lambda **kwargs:SimpleNamespace(functions=Functions()))
        @staticmethod
        def HTTPProvider(*args,**kwargs):return object()
        @staticmethod
        def to_checksum_address(value):return value
    monkeypatch.setattr(web3,'Web3',Web3Mock)
    settings=SimpleNamespace(polygon_rpc_url=SecretStr('http://mock.invalid'))
    wallet=SimpleNamespace(wallet_address='0x'+'1'*40)
    with pytest.raises(TradingError) as error:
        ComboGateway(settings,wallet).ensure_funding(SimpleNamespace(total_required=Decimal('10')), 'BUY')
    assert error.value.code=='balance_insufficient'
