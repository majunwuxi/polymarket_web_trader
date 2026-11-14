"""报价值为模拟数据，不触发资金交易。"""
from decimal import Decimal
from types import SimpleNamespace as S
import pytest
from backend.combo_quote import verify_quote, combo_position_id
from backend.trading_gateway import TradingError


def quote(**kwargs):
    data=dict(rfq_id='mock-rfq',direction='BUY',expires_at=2000,blended_price=Decimal('0.5'),maker_amount=Decimal('9'),taker_amount=Decimal('18'),total_required=Decimal('10'),net_receive=None)
    return S(**(data|kwargs))


def test_valid_and_expired_quote():
    verify_quote(quote(),rfq_id='mock-rfq',direction='BUY',quantity=Decimal('10'),now_ms=1999)
    with pytest.raises(TradingError):verify_quote(quote(),rfq_id='mock-rfq',direction='BUY',quantity=Decimal('10'),now_ms=2000)


def test_wrong_rfq_and_budget_are_rejected():
    for value in (quote(rfq_id='other'),quote(total_required=Decimal('10.01')),quote(blended_price=Decimal('NaN'))):
        with pytest.raises(TradingError):verify_quote(value,rfq_id='mock-rfq',direction='BUY',quantity=Decimal('10'),now_ms=1000)


def test_sell_requires_proceeds_and_correct_shares():
    value=quote(direction='SELL',maker_amount=Decimal('5'),total_required=Decimal('5'),net_receive=Decimal('2'))
    verify_quote(value,rfq_id='mock-rfq',direction='SELL',quantity=Decimal('5'),now_ms=1000)
    for value in (quote(direction='SELL',total_required=Decimal('6'),net_receive=Decimal('2')),quote(direction='SELL',net_receive=None)):
        with pytest.raises(TradingError):verify_quote(value,rfq_id='mock-rfq',direction='SELL',quantity=Decimal('5'),now_ms=1000)


def test_combo_position_identity_must_be_uint256_digits():
    assert combo_position_id(quote(position_id='123456'))=='123456'
    for bad in ('', '-1', 'abc', '１', str(2**256)):
        with pytest.raises(TradingError):combo_position_id(quote(position_id=bad))
