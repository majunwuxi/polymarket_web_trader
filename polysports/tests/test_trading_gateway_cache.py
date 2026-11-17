from types import SimpleNamespace

import pytest
from eth_account import Account

from backend import trading_gateway as module
from backend.config import Settings, WalletConfig, WalletSecrets


def wallet_settings():
    key='0x'+'3'*64
    wallet=Account.from_key(key).address.lower()
    config=WalletConfig(label='模拟',wallet_address=wallet,signer_address=wallet,
                        wallet_type='EOA',credential_ref='mock')
    settings=Settings(database_url='postgresql+psycopg://mock:mock@127.0.0.1/mock',
                      auth_secret='x'*40,public_origin='https://example.test',
                      wallet_credentials={'mock':WalletSecrets(private_key=key)})
    return settings,config


def test_gateway_reuses_sdk_client_and_closes_on_shutdown(monkeypatch):
    module.close_gateway_clients()
    settings,wallet=wallet_settings()
    created=[]

    class Client:
        def __init__(self):
            self.wallet=wallet.wallet_address
            self.wallet_type=wallet.wallet_type
            self.closed=0
        def close(self):
            self.closed+=1

    def create(**kwargs):
        client=Client();created.append(client);return client

    monkeypatch.setattr(module,'ensure_geography',lambda:None)
    monkeypatch.setattr(module.NoDeployClient,'create',staticmethod(create))
    try:
        with module.Gateway(settings,wallet) as first:
            assert first.client is created[0]
        with module.Gateway(settings,wallet) as second:
            assert second.client is created[0]
        assert len(created)==1 and created[0].closed==0
        module.close_gateway_clients()
        assert created[0].closed==1
    finally:
        module.close_gateway_clients()


def test_geography_check_is_reused_briefly(monkeypatch):
    module.close_gateway_clients()
    calls=[]
    def get(*args,**kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=200,json=lambda:{'blocked':False})
    monkeypatch.setattr(module.httpx,'get',get)
    try:
        module.ensure_geography();module.ensure_geography()
        assert len(calls)==1
    finally:
        module.close_gateway_clients()


def test_blocked_geography_is_reused_briefly(monkeypatch):
    module.close_gateway_clients()
    calls=[]
    def get(*args,**kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=200,json=lambda:{'blocked':True})
    monkeypatch.setattr(module.httpx,'get',get)
    try:
        with pytest.raises(module.TradingError,match='限制'):module.ensure_geography()
        with pytest.raises(module.TradingError,match='限制'):module.ensure_geography()
        assert len(calls)==1
    finally:
        module.close_gateway_clients()
