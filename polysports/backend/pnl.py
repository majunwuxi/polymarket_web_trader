"""交易记录卡片上的盈亏标签：列表直接可读，并明确区分已核实成本与估算成本。

口径与 `/trade-intents/{id}/resolution` 的 `settlement_amounts` 保持一致：
BUY 单按二元终态赔付（获胜每份 1 pUSD、未获胜 0）减去持仓成本；SELL 平仓按成交价
结算，不适用终态赔付，不在此标注盈亏。只有 CONFIRMED 成交明细完整覆盖已成交份额
时才采用真实成本；缺失时回退到「限价 × 份额」估算，并把标签写成「约…」，绝不把
估算值冒充已确认结算金额。

组合仓位没有单一二元 condition。已落库 `combo_fills` 的 CONFIRMED 成交明细时，
按签名接受订单的实际支出与份额给出不带「约」的确定盈亏；明细缺失时仍只用已确认
RFQ 报价里的投入与预计份额估算，并明确标为估算。组合按全中口径结算，实际赔付以
Polymarket 组合仓位终态为准。
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

_STEP = Decimal('0.0001')


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value: Decimal) -> str:
    """展示用金额：最多 4 位小数，去掉 Numeric(38,18) 带来的长串尾零。"""
    with localcontext() as context:
        context.prec = 60
        rounded = value.quantize(_STEP, rounding=ROUND_HALF_UP)
    text = format(rounded, 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text) or '0'


def _pending(reason):
    return {'amount': None, 'tone': 'pending', 'label': '盈亏待确认', 'basis': None,
            'estimated': False, 'detail': reason, 'cost': None, 'payout': None}


def _not_applicable(reason):
    return {'amount': None, 'tone': 'na', 'label': '平仓不适用', 'basis': None,
            'estimated': False, 'detail': reason, 'cost': None, 'payout': None}


def _result(*, pnl: Decimal, cost: Decimal, payout: Decimal, basis, estimated, detail):
    tone = 'profit' if pnl > 0 else 'loss' if pnl < 0 else 'flat'
    word = '盈利' if pnl > 0 else '亏损' if pnl < 0 else '持平'
    prefix = '约' if estimated else ''
    label = f'{prefix}{word} {_money(abs(pnl))} pUSD' if tone != 'flat' else f'{prefix}盈亏持平'
    return {'amount': _money(abs(pnl)), 'tone': tone, 'label': label, 'basis': basis,
            'estimated': estimated,
            'detail': detail, 'cost': _money(cost), 'payout': _money(payout)}


def _confirmed_cost(fills, shares: Decimal):
    """与 settlement_amounts 同源：CONFIRMED 明细完整覆盖成交份额才返回真实成本。"""
    confirmed = [x for x in (fills or []) if str(x.get('status', '')).upper() == 'CONFIRMED']
    total = Decimal(0)
    cost = Decimal(0)
    for item in confirmed:
        size = _decimal(item.get('size'))
        price = _decimal(item.get('price'))
        if size is None or price is None or size <= 0 or price <= 0 or price >= 1:
            return None
        total += size
        cost += size * price
    return cost if total == shares else None


def single_pnl(*, side, matched_size, limit_price, result_won, fills=None, score_verified=False):
    """单场 BUY 订单的卡片盈亏；赛果未确认或无法判断时不给出金额。"""
    if side != 'BUY':
        return _not_applicable('卖出平仓按成交价结算，不适用市场终态赔付，卡片不标注终态盈亏')
    if not score_verified or type(result_won) is not bool:
        return _pending('赛果尚未确认或市场命题无法判断，暂不标注盈亏')
    shares = _decimal(matched_size)
    if shares is None or shares <= 0:
        return _pending('订单没有已确认的成交份额，暂不标注盈亏')
    with localcontext() as context:
        context.prec = 60
        payout = shares if result_won else Decimal(0)
        cost = _confirmed_cost(fills, shares)
        if cost is not None:
            basis, estimated = 'confirmed_fills', False
            detail = (f'成交成本 {_money(cost)} pUSD（CONFIRMED 成交明细已覆盖 {_money(shares)} 份）'
                      f' · 终态收入 {_money(payout)} pUSD')
        else:
            price = _decimal(limit_price)
            if price is None or price <= 0 or price >= 1:
                return _pending('缺少可用的 CONFIRMED 成交明细与有效限价，成本无法核算')
            cost = shares * price
            basis, estimated = 'limit_estimate', True
            detail = (f'未取得完整 CONFIRMED 成交明细，成本按限价 {_money(price)} × '
                      f'{_money(shares)} 份估算为 {_money(cost)} pUSD；实际成本以成交记录为准')
        return _result(pnl=payout - cost, cost=cost, payout=payout,
                       basis=basis, estimated=estimated, detail=detail)


def combo_pnl(*, direction, by_score, quantity=None, quote=None, fill=None):
    """组合 BUY 的卡片盈亏：有 CONFIRMED 成交明细给确定金额，否则按报价估算并标「约」。"""
    if direction != 'BUY':
        return _not_applicable('组合卖出按报价成交，不适用终态胜负核对')
    if type(by_score) is not bool:
        return _pending('组合赛果尚未全部判断，暂不标注盈亏')
    fill = fill or {}
    cost = _decimal(fill.get('cost'))
    shares = _decimal(fill.get('size'))
    confirmed = (fill.get('state') == 'confirmed' and cost is not None and shares is not None
                 and cost > 0 and shares > 0)
    with localcontext() as context:
        context.prec = 60
        if confirmed:
            payout = shares if by_score else Decimal(0)
            origin = ('签名接受订单的 makerAmount/takerAmount'
                      if fill.get('source') == 'signed_order'
                      else '官方签名报价按 micro-USDC 精度重建的成交金额')
            detail = (f'成交成本 {_money(cost)} pUSD（{origin}）· 实际份额 {_money(shares)} 份'
                      f' · 终态收入 {_money(payout)} pUSD；'
                      f'组合按全中口径结算，不等于链上赎回或实际到账')
            basis, estimated = 'combo_fill', False
        else:
            quote = quote or {}
            cost = _decimal(quote.get('total_required'))
            if cost is None:
                cost = _decimal(quantity)
            shares = _decimal(quote.get('net_receive'))
            price = _decimal(quote.get('price'))
            if shares is None and cost is not None and cost > 0 and price is not None and 0 < price < 1:
                shares = cost / price
            if cost is None or cost <= 0 or shares is None or shares <= 0:
                return _pending('缺少已确认报价的投入或预计份额，组合盈亏无法估算')
            payout = shares if by_score else Decimal(0)
            detail = (f'该组合尚未落库 CONFIRMED 成交明细；按已确认报价投入 {_money(cost)} pUSD 与预计份额 '
                      f'{_money(shares)} 份估算，全中收入 {_money(payout)} pUSD；'
                      f'实际赔付以 Polymarket 组合仓位终态为准')
            basis, estimated = 'quote_estimate', True
        return _result(pnl=payout - cost, cost=cost, payout=payout,
                       basis=basis, estimated=estimated, detail=detail)
