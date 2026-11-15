"""卡片盈亏标签：金额、配色语义与「已核实 / 估算」口径。"""
from backend.pnl import combo_pnl, single_pnl


def test_single_loss_uses_limit_estimate_and_marks_it_as_estimate():
    pnl = single_pnl(side='BUY', matched_size='5.12', limit_price='0.41',
                     result_won=False, fills=[], score_verified=True)
    assert pnl['tone'] == 'loss'
    assert pnl['amount'] == '2.0992'
    assert pnl['label'] == '约亏损 2.0992 pUSD'
    assert pnl['basis'] == 'limit_estimate' and pnl['estimated'] is True
    assert pnl['cost'] == '2.0992' and pnl['payout'] == '0'
    assert '限价' in pnl['detail']


def test_single_win_with_confirmed_fills_uses_real_cost_without_estimate_prefix():
    fills = [{'size': '5.12', 'price': '0.4', 'status': 'CONFIRMED'}]
    pnl = single_pnl(side='BUY', matched_size='5.12', limit_price='0.41',
                     result_won=True, fills=fills, score_verified=True)
    assert pnl['tone'] == 'profit'
    assert pnl['amount'] == '3.072'
    assert pnl['label'] == '盈利 3.072 pUSD'
    assert pnl['basis'] == 'confirmed_fills' and pnl['estimated'] is False
    assert pnl['cost'] == '2.048' and pnl['payout'] == '5.12'


def test_partial_or_unconfirmed_fills_fall_back_to_limit_estimate():
    for fills in ([{'size': '2', 'price': '0.4', 'status': 'CONFIRMED'}],
                  [{'size': '5.12', 'price': '0.4', 'status': 'PENDING'}],
                  [{'size': '5.12', 'price': '0.4', 'status': 'CONFIRMED'},
                   {'size': '1', 'price': '0.4', 'status': 'CONFIRMED'}]):
        pnl = single_pnl(side='BUY', matched_size='5.12', limit_price='0.41',
                         result_won=False, fills=fills, score_verified=True)
        assert pnl['basis'] == 'limit_estimate' and pnl['estimated'] is True
        assert pnl['label'].startswith('约亏损')


def test_single_without_verified_result_or_valid_numbers_gives_no_amount():
    cases = [
        dict(result_won=None, score_verified=False),
        dict(result_won=None, score_verified=True),
        dict(result_won=True, score_verified=False),
        dict(result_won=True, score_verified=True, matched_size='0'),
        dict(result_won=True, score_verified=True, limit_price=None),
        dict(result_won=True, score_verified=True, limit_price='0'),
    ]
    for case in cases:
        kwargs = dict(side='BUY', matched_size='5.12', limit_price='0.41',
                      result_won=False, fills=[], score_verified=True)
        kwargs.update(case)
        pnl = single_pnl(**kwargs)
        assert pnl['amount'] is None and pnl['tone'] == 'pending', case
    sell = single_pnl(side='SELL', matched_size='5.12', limit_price='0.41',
                      result_won=True, fills=[], score_verified=True)
    assert sell['tone'] == 'na' and sell['amount'] is None


def test_combo_win_and_loss_are_quote_estimates():
    quote = {'price': '0.5', 'total_required': '1.5', 'net_receive': '3'}
    won = combo_pnl(direction='BUY', by_score=True, quantity='1.5', quote=quote)
    assert won['tone'] == 'profit' and won['amount'] == '1.5'
    assert won['label'] == '约盈利 1.5 pUSD'
    assert won['basis'] == 'quote_estimate' and won['estimated'] is True
    lost = combo_pnl(direction='BUY', by_score=False, quantity='1.5', quote=quote)
    assert lost['tone'] == 'loss' and lost['label'] == '约亏损 1.5 pUSD'


def test_combo_derives_shares_from_price_when_net_receive_missing():
    pnl = combo_pnl(direction='BUY', by_score=True, quantity='1.5',
                    quote={'price': '0.5', 'total_required': '1.5', 'net_receive': None})
    assert pnl['tone'] == 'profit' and pnl['amount'] == '1.5'


def test_combo_flat_pending_and_sell_cases():
    flat = combo_pnl(direction='BUY', by_score=True,
                     quote={'price': '1', 'total_required': '1', 'net_receive': '1'})
    assert flat['tone'] == 'flat' and flat['amount'] == '0'
    pending = combo_pnl(direction='BUY', by_score=None, quantity='1.5', quote=None)
    assert pending['tone'] == 'pending' and pending['amount'] is None
    sell = combo_pnl(direction='SELL', by_score=True, quantity='1.5')
    assert sell['tone'] == 'na' and sell['amount'] is None


def test_long_numeric_decimals_are_shortened_for_display():
    pnl = single_pnl(side='BUY', matched_size='5.120000000000000000',
                     limit_price='0.410000000000000000', result_won=False,
                     fills=[], score_verified=True)
    assert pnl['amount'] == '2.0992'
    assert pnl['label'] == '约亏损 2.0992 pUSD'
