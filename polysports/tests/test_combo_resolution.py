"""组合胜负核对均为模拟数据，不表示真实市场或真实交易。"""
from backend.combo_resolution import leg_agreement, summarize_combo


def leg(result, market):
    return {'result_won': result, 'market_won': market}


def test_all_legs_win_confirms_combo():
    out = summarize_combo([leg(True, True), leg(True, True)])
    assert out['by_score'] is True and out['by_market'] is True
    assert out['agreement'] == 'consistent' and '一致确认组合获胜' in out['reason']
    assert out['leg_count'] == 2 and out['judged_legs'] == 2 and out['resolved_legs'] == 2


def test_any_losing_leg_makes_combo_lose():
    out = summarize_combo([leg(True, True), leg(False, False)])
    assert out['by_score'] is False and out['by_market'] is False
    assert out['agreement'] == 'consistent' and '已有腿未命中' in out['reason']


def test_unjudged_or_pending_legs_do_not_confirm_combo():
    out = summarize_combo([leg(True, True), leg(None, None)])
    assert out['by_score'] is None and out['by_market'] is None
    assert out['agreement'] == 'unavailable'
    # 仍有一腿市场未终态：赛果结论只能是预计值。
    out = summarize_combo([leg(True, True), leg(True, None)])
    assert out['by_score'] is True and out['by_market'] is None
    assert out['agreement'] == 'result_only' and '预计' in out['reason']
    # 已有腿终态未获胜，即便其他腿未结算，组合也已确定未获胜。
    out = summarize_combo([leg(False, False), leg(True, None)])
    assert out['by_market'] is False and out['by_score'] is False
    assert out['agreement'] == 'consistent'


def test_conflicting_and_single_results():
    out = summarize_combo([leg(True, False), leg(True, True)])
    assert out['by_score'] is True and out['by_market'] is False
    assert out['agreement'] == 'conflict' and '人工核实' in out['reason']
    out = summarize_combo([leg(None, True), leg(None, True)])
    assert out['agreement'] == 'market_only' and out['by_market'] is True
    assert out['by_score'] is None


def test_no_legs_and_sell_combo():
    empty = summarize_combo([])
    assert empty['agreement'] == 'unavailable' and '没有可核对的腿' in empty['reason']
    sell = summarize_combo([leg(True, True)], direction='SELL')
    assert sell['applicable'] is False and sell['agreement'] == 'not_applicable'
    assert sell['by_score'] is None and sell['by_market'] is None


def test_notice_states_no_combo_settlement_amount():
    out = summarize_combo([leg(True, True)])
    assert '全中口径' in out['notice'] and '不计算组合结算金额' in out['notice']
    assert '市场终态' in out['notice'] and '官方组合仓位终态' in out['notice']


def test_leg_agreement_levels_and_type_safety():
    assert leg_agreement(leg(True, True)) == 'consistent'
    assert leg_agreement(leg(True, False)) == 'conflict'
    assert leg_agreement(leg(True, None)) == 'partial'
    assert leg_agreement(leg(None, None)) == 'unavailable'
    # 字符串/数字等非 bool 结果一律不猜，按缺失处理。
    assert leg_agreement({'result_won': 'true', 'market_won': True}) == 'partial'
    assert leg_agreement({'result_won': 1, 'market_won': 0}) == 'unavailable'
