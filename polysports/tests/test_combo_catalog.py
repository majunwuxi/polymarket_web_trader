"""使用明确标识的模拟目录验证组合市场映射。"""
from types import SimpleNamespace as S
from uuid import uuid4

import pytest
from backend.combo_catalog import resolve_positions, resolve_market_details, fetch_positions
from backend.trading_gateway import TradingError


def fixtures():
    market = S(condition_id='0x'+'a'*64, gamma_market_id='123')
    outcome = S(id=uuid4(), outcome_index=0, label='模拟主队')
    entry = S(id='123', condition_id=market.condition_id,
              outcomes=S(yes=S(label='模拟主队',position_id='123456'),
                         no=S(label='模拟客队',position_id='123457')))
    return market,outcome,entry


def page(*items,limit_reached=False):
    return S(items=items,limit_reached=limit_reached)


def test_matches_only_catalog_position_not_clob_asset():
    market,outcome,entry=fixtures()
    assert resolve_positions([market],[outcome],[page(entry)])=={outcome.id:'123456'}


def test_rejects_ambiguous_incomplete_and_wrong_identity():
    market,outcome,entry=fixtures()
    for pages in ([page(entry,entry)], [page(entry,limit_reached=True)], [page()]):
        with pytest.raises(TradingError): resolve_positions([market],[outcome],pages)
    entry.id='456'
    with pytest.raises(TradingError): resolve_positions([market],[outcome],[page(entry)])


def test_rejects_different_label_and_invalid_position():
    market,outcome,entry=fixtures()
    entry.outcomes.yes.label='其他模拟球队'
    with pytest.raises(TradingError): resolve_positions([market],[outcome],[page(entry)])
    entry.outcomes.yes.label=outcome.label
    entry.outcomes.yes.position_id='not-a-position'
    with pytest.raises(TradingError): resolve_positions([market],[outcome],[page(entry)])


def test_duplicate_outcome_is_rejected():
    market,outcome,entry=fixtures()
    with pytest.raises(TradingError):
        resolve_positions([market,market],[outcome,outcome],[page(entry)])


def detail(entry):
    return S(id=entry.id,condition_id=entry.condition_id,outcomes=entry.outcomes,
             state=S(combo_status='enabled',active=True,closed=False))


def test_direct_market_detail_uses_combo_position_and_checks_status():
    market,outcome,entry=fixtures();source=detail(entry)
    assert resolve_market_details([market],[outcome],[source])=={outcome.id:'123456'}
    for changes in ({'combo_status':'disabled'}, {'combo_status':None}, {'active':False}, {'closed':None}):
        source=detail(entry)
        for key,value in changes.items():setattr(source.state,key,value)
        with pytest.raises(TradingError,match='组合'):resolve_market_details([market],[outcome],[source])


def test_direct_detail_rejects_wrong_identity_labels_and_missing_positions():
    market,outcome,entry=fixtures();source=detail(entry)
    source.id='999'
    with pytest.raises(TradingError,match='身份'):resolve_market_details([market],[outcome],[source])
    source.id=market.gamma_market_id;source.condition_id='0x'+'b'*64
    with pytest.raises(TradingError,match='身份'):resolve_market_details([market],[outcome],[source])
    source.condition_id=market.condition_id;source.outcomes.yes.label='其他球队'
    with pytest.raises(TradingError,match='选项'):resolve_market_details([market],[outcome],[source])
    source.outcomes.yes.label=outcome.label;source.outcomes.no.position_id=None
    with pytest.raises(TradingError,match='标识'):resolve_market_details([market],[outcome],[source])


def test_fetch_positions_queries_market_directly_without_paginating(monkeypatch):
    from backend import combo_catalog
    market,outcome,entry=fixtures();calls=[]
    class MockClient:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get_market(self,*,id):calls.append(id);return detail(entry)
        def list_combo_markets(self,**kwargs):raise AssertionError('不应扫描完整目录')
    monkeypatch.setattr(combo_catalog,'PublicClient',MockClient)
    assert fetch_positions([market],[outcome])=={outcome.id:'123456'}
    assert calls==['123']
