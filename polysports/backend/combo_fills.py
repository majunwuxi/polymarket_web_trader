"""组合成交明细的确定口径与状态流转。

SDK 明确定义 BUY 报价的 ``maker_amount`` 是签名接受订单实际支出的抵押品、
``taker_amount`` 是实际收到的组合份额，``total_required`` 只是接受报价所需的
余额（可能高于实际支出），``blended_price`` 是每份价格。因此：
- 新组合在接受报价时直接落库 maker/taker（source='signed_order'）；
- 迁移前的历史组合用已落库的 blended_price × taker_amount 按 micro-USDC
  精度重建 makerAmount（source='quote_rebuilt'），并用 total_required 校验。

只有 RFQ 进入 CONFIRMED 后明细才置为 confirmed；终态失败则作废，绝不把
未成交或结果不明的报价当成已确认盈亏。
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from sqlalchemy import select

from .models import ComboFill

_MICRO = Decimal('0.000001')


def rebuilt_cost(price, shares, required):
    """从官方签名报价重建链上 makerAmount；不满足校验时不猜测，返回 None。

    链上金额按 micro-USDC 取整（向上取整，与 SDK 的 makerAmount 计算一致）。
    total_required >= makerAmount 是 SDK 保证的关系，用作反向校验。
    """
    try:
        p = Decimal(str(price))
        s = Decimal(str(shares))
        r = Decimal(str(required))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not all(x.is_finite() for x in (p, s, r)):
        return None
    if not (Decimal(0) < p < Decimal(1)) or s <= 0 or r <= 0:
        return None
    cost = (p * s).quantize(_MICRO, rounding=ROUND_CEILING)
    if cost <= 0 or cost > r:
        return None
    return cost


def fill_payload(fill):
    """把 ORM 明细转成 pnl/接口可用的普通字典；非 confirmed 一律视为无明细。"""
    if fill is None:
        return None
    return {'cost': str(fill.cost), 'size': str(fill.size), 'source': fill.source,
            'state': fill.state, 'tx_hash': fill.tx_hash,
            'confirmed_at': fill.confirmed_at.isoformat() if fill.confirmed_at else None}


def confirm_combo_fill(db, row, now):
    """RFQ 进入 CONFIRMED：确认已有明细，或为历史组合从签名报价重建明细。"""
    if row.direction != 'BUY':
        return None
    fill = db.scalar(select(ComboFill).where(ComboFill.rfq_id == row.id))
    if fill is not None:
        if fill.state == 'pending':
            fill.state = 'confirmed'
            fill.confirmed_at = now
            fill.tx_hash = row.tx_hash or fill.tx_hash
        return fill
    cost = rebuilt_cost(row.quote_price, row.quote_receive, row.quote_required)
    if cost is None or row.quote_receive is None:
        return None
    fill = ComboFill(id=uuid.uuid4(), rfq_id=row.id, quote_id=row.quote_id, direction='BUY',
                     cost=cost, size=row.quote_receive, source='quote_rebuilt', state='confirmed',
                     tx_hash=row.tx_hash, confirmed_at=now)
    db.add(fill)
    return fill


def void_combo_fill(db, row):
    """RFQ 终态失败/过期/拒绝：作废尚未确认的明细。"""
    fill = db.scalar(select(ComboFill).where(ComboFill.rfq_id == row.id))
    if fill is not None and fill.state == 'pending':
        fill.state = 'void'
    return fill
