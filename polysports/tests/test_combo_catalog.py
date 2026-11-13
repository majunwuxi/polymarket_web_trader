"""使用明确标识的模拟目录验证组合市场映射。"""
from types import SimpleNamespace as S
from uuid import uuid4

import pytest
from backend.combo_catalog import resolve_positions
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
