"""组合腿端点使用模拟目录；不进行真实交易。"""
from test_zzz_trading_integration import env  # noqa: F401
from test_z_auth_integration import pg  # noqa: F401
from backend import combo_api
from backend.trading_gateway import TradingError


def test_combo_leg_check_requires_confirmed_mapping_and_catalog(env, monkeypatch):
    pg,client,headers,body=env
    monkeypatch.setattr(combo_api, 'fetch_positions', lambda markets,outcomes: {outcomes[0].id:'123456'})
    url='/api/v1/combo-legs/'+body['market_link_id']+'/'+body['outcome_id']
    response=client.get(url)
    assert response.status_code==200
    assert response.json()['data']['position_id']=='123456'
    assert response.json()['data']['trading_available'] is False
    def unavailable(*args): raise TradingError('combo_catalog_unavailable','模拟来源不可用')
    monkeypatch.setattr(combo_api,'fetch_positions',unavailable)
    response=client.get(url)
    assert response.status_code==503 and response.json()['error']['code']=='COMBO_CATALOG_UNAVAILABLE'
