"""组合买入三项摘要的口径：份额、每份价格、买入金额，且绝不混用预算与余额要求。"""
from __future__ import annotations

from backend.combo_fills import buy_summary


def _quote(**overrides):
    values = dict(price='0.41', total_required='2.2', net_receive='5.12')
    values.update(overrides)
    return values


def _fill(**overrides):
    values = dict(state='confirmed', cost='2.0992', size='5.12', source='signed_order')
    values.update(overrides)
    return values


def test_confirmed_fill_gives_exact_size_price_and_cost():
    summary = buy_summary('BUY', _quote(total_required='99'), _fill())
    assert summary['size'] == '5.12'
    # 每份价格 = makerAmount / takerAmount，不是签名报价的 blended_price。
    assert summary['price'] == '0.41'
    assert summary['cost'] == '2.0992'
    assert summary['basis'] == 'combo_fill'
    assert summary['estimated'] is False
    assert summary['source'] == 'signed_order'
    assert 'makerAmount' in summary['detail']


def test_summary_trims_numeric_long_tails():
    summary = buy_summary('BUY', None, _fill(cost='1.500000000000000000',
                                             size='1.500000000000000000'))
    assert summary['size'] == '1.5'
    assert summary['cost'] == '1.5'
    assert summary['price'] == '1'


def test_summary_never_uses_total_required_or_quantity_as_cost():
    # total_required 只是接受报价所需余额；缺明细时金额按 blended_price × 份额重建。
    summary = buy_summary('BUY', _quote(price='0.4', net_receive='3', total_required='9'), None)
    assert summary['cost'] == '1.2'
    assert summary['cost'] != '9'
    assert summary['basis'] == 'quote_rebuilt'
    assert summary['estimated'] is True


def test_missing_fill_rebuilds_from_signed_quote_with_micro_usdc():
    summary = buy_summary('BUY', _quote(price='0.333333', net_receive='3',
                                        total_required='2'), None)
    assert summary['size'] == '3'
    assert summary['price'] == '0.333333'
    assert summary['cost'] == '0.999999'
    assert summary['estimated'] is True


def test_invalid_quote_check_keeps_cost_unknown():
    summary = buy_summary('BUY', _quote(price='0.9', net_receive='10',
                                        total_required='1'), None)
    assert summary['size'] == '10'
    assert summary['price'] == '0.9'
    assert summary['cost'] is None
    assert summary['basis'] == 'quote_partial'
    assert '余额' in summary['detail']


def test_without_fill_or_quote_all_values_are_unknown():
    summary = buy_summary('BUY', None, None)
    assert summary['size'] is None and summary['price'] is None and summary['cost'] is None
    assert summary['basis'] == 'unavailable'


def test_pending_or_void_fill_falls_back_to_quote():
    summary = buy_summary('BUY', _quote(), _fill(state='pending'))
    assert summary['basis'] == 'quote_rebuilt'
    assert summary['cost'] == '2.0992'


def test_sell_has_no_buy_summary():
    assert buy_summary('SELL', _quote(), _fill()) is None
