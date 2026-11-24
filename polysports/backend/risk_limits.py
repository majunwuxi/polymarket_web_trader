from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext

from sqlalchemy import text

from .models import TradeLimitSettingsRow
from .trading_gateway import TradingError


def configured_limits(db, defaults):
    return db.get(TradeLimitSettingsRow, 1) or defaults


def limit_snapshot(db, settings, wallet_id):
    now = db.execute(text('SELECT clock_timestamp()')).scalar_one().astimezone(timezone(timedelta(hours=8)))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    daily = settings.max_daily_usd
    used = Decimal(0)
    if daily is not None:
        used = db.execute(
            text('''SELECT COALESCE(SUM(amount), 0) FROM trade_budget_reservations
                    WHERE wallet_id = :wallet_id AND day = :day'''),
            {'wallet_id': wallet_id, 'day': start.date()},
        ).scalar_one()
    return {
        'order_limit_percent': settings.order_limit_percent,
        'order_limit_floor_usd': settings.order_limit_floor_usd,
        'max_daily_usd': daily,
        'remaining_daily_usd': max(Decimal(0), daily - used) if daily is not None else None,
        'resets_at': start + timedelta(days=1),
        'day': start.date(),
    }


def check_buy_limit(settings, amount, available):
    percent = settings.order_limit_percent
    if percent is None:
        return
    if available is None:
        raise TradingError('balance_unavailable', '无法读取可用余额，不能校验单笔上限')
    try:
        balance = Decimal(str(available))
    except InvalidOperation as exc:
        raise TradingError('balance_unavailable', '可用余额无效，不能校验单笔上限') from exc
    if not balance.is_finite() or balance < 0:
        raise TradingError('balance_unavailable', '可用余额无效，不能校验单笔上限')
    with localcontext() as ctx:
        ctx.prec = 80
        ceiling = min(balance, max(settings.order_limit_floor_usd, balance * percent / 100))
    if amount > ceiling:
        formatted = format(ceiling, 'f')
        if '.' in formatted:
            formatted = formatted.rstrip('0').rstrip('.')
        raise TradingError('order_limit_exceeded', f'超过单笔买入上限 {formatted} pUSD')


def reserve_buy_budget(db, defaults, wallet_id, order_id, amount, available):
    db.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended('polysports:trade-limit-settings', 0))"))
    settings = configured_limits(db, defaults)
    check_buy_limit(settings, amount, available)
    # Shared lock across single and combo orders, held until the intent and reservation commit.
    db.execute(
        text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': f'polysports:buy-budget:{wallet_id}'}
    )
    limits = limit_snapshot(db, settings, wallet_id)
    remaining = limits['remaining_daily_usd']
    if remaining is not None and amount > remaining:
        raise TradingError('daily_limit_exceeded', f'超过今日买入剩余额度 {remaining} pUSD')
    db.execute(
        text('''INSERT INTO trade_budget_reservations (order_id, wallet_id, day, amount)
                VALUES (:order_id, :wallet_id, :day, :amount)'''),
        {'order_id': order_id, 'wallet_id': wallet_id, 'day': limits['day'], 'amount': amount},
    )


def release_buy_budget(db, order_id):
    db.execute(text('DELETE FROM trade_budget_reservations WHERE order_id = :order_id'), {'order_id': order_id})
