"""组合（串关）只读胜负核对：逐腿双结果 + 全中口径汇总。

组合不是单一二元市场：每条腿各自有「按已绑定比分推断的赛果结果」与
「Polymarket 底层市场 condition 的终态结算结果」。按串关口径，全部腿命中
才算组合获胜；任一腿明确未命中即整单未获胜；只要还有腿无法判断或尚未终态，
就不确认整单结论，也不计算金额——本系统未持久化组合成交份额，组合仓位终态
以官方为准，绝不用询价预计份额冒充成交份额。

赛事终态（比赛结束 + 外部赛果绑定核实）与市场终态（链上结算）互不等价；
同一组合内不同腿可以处在不同结算阶段，因此逐腿展示，不做整体猜测。
"""

from __future__ import annotations

_NOTICE = (
    '组合按全中口径核对：全部腿命中才算获胜，任一腿未命中即整单未获胜；'
    '任一腿无法判断或尚未终态，整单结论不确认。赛事终态（比赛结束）与市场终态'
    '（该盘口链上结算）互不等价；组合仓位没有单一二元 condition，'
    '结果二由各腿底层市场的 Polymarket 终态结算汇总，实际赔付以官方组合仓位终态为准；'
    '本系统未保存组合成交明细，不计算组合结算金额。'
)


def _won(leg, key):
    value = leg.get(key)
    return value if type(value) is bool else None


def leg_agreement(leg):
    """单腿两结果的核对口径：一致、冲突、仅有一个结果、两者皆无。"""
    result, market = _won(leg, 'result_won'), _won(leg, 'market_won')
    if result is None and market is None:
        return 'unavailable'
    if result is None or market is None:
        return 'partial'
    return 'consistent' if result == market else 'conflict'


def summarize_combo(legs, *, direction='BUY'):
    """把逐腿的双结果汇总成组合层的两个独立结果，并交叉印证。

    by_score / by_market 为 bool 或 None（None 表示不能确认，不等于输）。
    agreement 取值：consistent / conflict / market_only / result_only /
    unavailable / not_applicable（SELL 平仓）。
    """
    rows = list(legs or [])
    summary = {
        'applicable': direction == 'BUY',
        'by_score': None,
        'by_market': None,
        'agreement': 'unavailable',
        'reason': None,
        'notice': _NOTICE,
        'leg_count': len(rows),
        'judged_legs': 0,
        'resolved_legs': 0,
    }
    if direction != 'BUY':
        summary['agreement'] = 'not_applicable'
        summary['reason'] = '组合卖出按报价成交，不适用终态胜负核对'
        return summary
    scores = [_won(x, 'result_won') for x in rows]
    markets = [_won(x, 'market_won') for x in rows]
    summary['judged_legs'] = sum(x is not None for x in scores)
    summary['resolved_legs'] = sum(x is not None for x in markets)
    # 赛果：任一腿明确未命中即整单未获胜；只有全部腿都能判断且全中才确认获胜。
    if rows and any(x is False for x in scores):
        summary['by_score'] = False
    elif rows and all(x is True for x in scores):
        summary['by_score'] = True
    # 市场：任一腿终态未获胜即整单未获胜；全部腿终态且全中才确认获胜。
    if rows and any(x is False for x in markets):
        summary['by_market'] = False
    elif rows and all(x is True for x in markets):
        summary['by_market'] = True
    score, market = summary['by_score'], summary['by_market']
    if not rows:
        summary['reason'] = '组合没有可核对的腿'
    elif score is not None and market is not None:
        if score == market:
            summary['agreement'] = 'consistent'
            summary['reason'] = '两个结果一致确认组合获胜' if score else '已有腿未命中，两个结果一致确认组合未获胜'
        else:
            summary['agreement'] = 'conflict'
            summary['reason'] = '两个结果不一致，需人工核实，不确认组合胜负'
    elif market is not None:
        summary['agreement'] = 'market_only'
        summary['reason'] = '赛果规则无法自动判断或外部赛果未核实，以各腿市场终态为准，建议人工核实'
    elif score is not None:
        summary['agreement'] = 'result_only'
        summary['reason'] = '各腿 Polymarket 终态尚未全部返回；按赛果推算的组合结果仅为预计值，待市场结算确认'
    else:
        summary['reason'] = '赛果与各腿市场终态均不足以判断，暂不确认组合胜负'
    return summary
