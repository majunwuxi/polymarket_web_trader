"""每日盈亏日历：按北京时间 12:00 起算归日，聚合单场与组合的投入、返奖与已结算盈亏。

口径与交易记录卡片完全同源（`pnl.single_pnl` / `pnl.combo_pnl`），不查询 Polymarket
实时终态，也不使用未持久化的结算时间戳：归日只看下单时间 `created_at`，
北京时间 12:00 至次日 11:59 记为同一天。

「投入」合计当日全部下单成本（含未结算）；「返奖」和「盈亏」只统计已结算部分，
未结算投注单独计数。盈亏为按市场规则推算的应结算金额，不含未记录费用，
不等于链上赎回或实际到账。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from .combo_api import combo_data, combo_prefetch
from .models import ComboRFQ, TradeFill, TradeIntent
from .order_resolution import judge_score_detail
from .pnl import single_pnl
from .prefetch import rows_by_attr
from .trading import prefetch_intents

TZ8 = timezone(timedelta(hours=8))
CALENDAR_SHIFT = timedelta(hours=12)
STEP = Decimal('0.0001')
SPORT_ORDER = ('football', 'nba', 'mixed')
SPORT_NAMES = {'football': '足球', 'nba': '篮球', 'mixed': '跨运动组合'}
SETTLED_TONES = ('profit', 'loss', 'flat')

# 一笔投注在日历里的结算状态，三档互斥、不可混用：
# - settled   已结算且盈亏可严格核实，计入返奖与盈亏；
# - unpriced  已结算（比赛结束且赛果已核实），但盘口口径无法按比分严格推断，
#             盈亏待核实，只计投入、不计入返奖与盈亏合计，绝不猜测金额；
# - pending   赛果尚未核实，仍在等待结算。
SETTLED = 'settled'
UNPRICED = 'unpriced'
PENDING = 'pending'


class CalendarError(ValueError):
    """月份或时间范围参数无效。"""


def _dec(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _money(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 60
        rounded = value.quantize(STEP, rounding=ROUND_HALF_UP)
    text = format(rounded, 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text) or '0'


def calendar_day(created_at: datetime) -> date:
    """北京时间 12:00 起算：12:00 至次日 11:59 记为同一天。"""
    return (created_at.astimezone(TZ8) - CALENDAR_SHIFT).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """某个归日对应的 [start, end) 时间区间（含时区）。"""
    start = datetime.combine(day, time(12, 0), tzinfo=TZ8)
    return start, start + timedelta(days=1)


def month_bounds(month: str) -> tuple[date, date, datetime, datetime]:
    """把 ``YYYY-MM`` 解析成首日、次月首日与 [start, end) 区间。"""
    try:
        year, mon = (int(part) for part in month.split('-'))
        first = date(year, mon, 1)
    except (AttributeError, TypeError, ValueError):
        raise CalendarError('月份格式应为 YYYY-MM') from None
    following = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    start = datetime.combine(first, time(12, 0), tzinfo=TZ8)
    end = datetime.combine(following, time(12, 0), tzinfo=TZ8)
    return first, following, start, end


def _settled(pnl) -> bool:
    return bool(pnl and pnl.get('tone') in SETTLED_TONES)


def _sport_of_combo(data) -> str | None:
    sports = {leg.get('sport') for leg in data.get('legs') or [] if leg.get('sport')}
    if not sports:
        return None
    return next(iter(sports)) if len(sports) == 1 else 'mixed'


def single_item(intent, related, fills) -> dict | None:
    """一笔单场订单的日历条目；无法确定运动或订单时返回 None。"""
    game = related.get('game')
    if game is None:
        return None
    market = related.get('market')
    outcome = related.get('outcome')
    home = related.get('home')
    away = related.get('away')
    binding = related.get('binding')
    verified = bool(
        binding
        and binding.state == 'bound'
        and game.status == 'finished'
        and game.home_score is not None
        and game.away_score is not None
    )
    won, _ = (
        judge_score_detail(
            sport=game.sport,
            question=getattr(market, 'question', None),
            outcome_label=getattr(outcome, 'label', None),
            home=getattr(home, 'source_name', None),
            away=getattr(away, 'source_name', None),
            home_score=game.home_score if verified else None,
            away_score=game.away_score if verified else None,
            provider_status=game.provider_status,
        )
        if verified
        else (None, '外部赛果尚未绑定核实或比分未确认')
    )
    payload = [
        {'size': fill.size, 'price': fill.price, 'status': fill.status} for fill in fills
    ]
    pnl = single_pnl(
        side=intent.side,
        matched_size=intent.matched_size,
        limit_price=intent.limit_price,
        result_won=won,
        fills=payload,
        score_verified=verified,
    )
    settled = _settled(pnl)
    # 赛果已核实、但盘口口径无法按比分严格推断时，single_pnl 停在 pending。
    # 这笔投注其实已经结算，只是盈亏待核实；必须与「赛果未核实」分开，
    # 否则同一天既显示盈亏又标待结算，自相矛盾。
    unpriced = verified and won is None
    state = SETTLED if settled else UNPRICED if unpriced else PENDING
    cost = _dec(pnl.get('cost'))
    estimated = bool(pnl.get('estimated'))
    if cost is None:
        price = _dec(intent.limit_price) or Decimal(0)
        shares = _dec(intent.matched_size)
        budget = _dec(intent.requested_size) or Decimal(0)
        cost = shares * price if shares and shares > 0 else budget * price
        estimated = True
    return {
        'kind': 'single',
        'sport': game.sport,
        'bet': {
            'id': str(intent.id),
            'won': won,
            'legs': [{
                'home': getattr(home, 'name_zh', None) or getattr(home, 'source_name', None),
                'away': getattr(away, 'name_zh', None) or getattr(away, 'source_name', None),
                'market_question': getattr(market, 'question', None),
                'outcome_label': getattr(outcome, 'label', None),
                'result_won': won,
            }],
        } if isinstance(won, bool) and (_dec(intent.matched_size) or Decimal(0)) > 0 else None,
        'state': state,
        'cost': cost,
        'payout': _dec(pnl.get('payout')) if settled else None,
        'estimated': estimated,
    }


def combo_item(row, data, *, expected_legs: int) -> dict | None:
    """一笔组合（过关票）的日历条目；跨运动组合归入 mixed，不重复计入足球或篮球。"""
    sport = _sport_of_combo(data)
    if sport is None:
        return None
    pnl = data.get('pnl') or {}
    settled = _settled(pnl)
    # 各腿赛果已核实但汇总口径仍无法确认胜负时，同单场一样视为已结算、盈亏待核实。
    legs = data.get('legs') or []
    judged = [leg for leg in legs if leg.get('result_won') is not None]
    unpriced = bool(judged and not settled and pnl.get('tone') != 'na')
    state = SETTLED if settled else UNPRICED if unpriced else PENDING
    summary = data.get('buy_summary') or {}
    won = data.get('by_score')
    if won is True and len(legs) != expected_legs:
        won = None
    cost = _dec(summary.get('cost'))
    estimated = bool(summary.get('estimated'))
    if cost is None:
        quote = data.get('quote') or {}
        cost = _dec(quote.get('total_required')) or _dec(row.quantity) or Decimal(0)
        estimated = True
    return {
        'kind': 'combo',
        'sport': sport,
        'bet': {
            'id': str(row.id),
            'won': won,
            'legs': [
                {
                    key: leg.get(key)
                    for key in ('home', 'away', 'market_question', 'outcome_label', 'result_won')
                }
                for leg in legs
            ],
        } if row.state == 'confirmed' and isinstance(won, bool) else None,
        'state': state,
        'cost': cost,
        'payout': _dec(pnl.get('payout')) if settled else None,
        'estimated': estimated,
    }


def collect_items(db: Session, start: datetime, end: datetime) -> list[dict]:
    """取 [start, end) 内全部有效投注，产出日历条目。"""
    items: list[dict] = []

    intents = list(
        db.scalars(
            select(TradeIntent).where(
                TradeIntent.created_at >= start,
                TradeIntent.created_at < end,
                TradeIntent.side == 'BUY',
                TradeIntent.state.notin_(('rejected', 'cancelled')),
            )
        )
    )
    if intents:
        related = prefetch_intents(db, intents)
        fills = rows_by_attr(db, TradeFill, 'intent_id', [intent.id for intent in intents])
        for intent in intents:
            item = single_item(intent, related.get(intent.id) or {}, fills.get(intent.id, []))
            if item is not None:
                item['day'] = calendar_day(intent.created_at)
                items.append(item)

    combos = list(
        db.scalars(
            select(ComboRFQ).where(
                ComboRFQ.created_at >= start,
                ComboRFQ.created_at < end,
                ComboRFQ.direction == 'BUY',
                ComboRFQ.state.notin_(('rejected', 'failed', 'expired')),
            )
        )
    )
    if combos:
        prefetch = combo_prefetch(db, combos)
        for row in combos:
            item = combo_item(
                row, combo_data(db, row, prefetch=prefetch),
                expected_legs=len(prefetch['legs'].get(row.id, [])),
            )
            if item is not None:
                item['day'] = calendar_day(row.created_at)
                items.append(item)
    return items


def _bucket() -> dict:
    return {
        'invested': Decimal(0),
        'settled_invested': Decimal(0),
        'returned': Decimal(0),
        'settled_count': 0,
        'unpriced_count': 0,
        'pending_count': 0,
        'has_estimate': False,
    }


def _add(bucket: dict, item: dict) -> None:
    state = item['state']
    if state == UNPRICED:
        # 盈亏待核实：投入与盈亏都按 0 计入合计，只保留笔数标注。
        # 金额无处核实，既不并入 invested，也不并入 settled_invested。
        bucket['unpriced_count'] += 1
        return
    bucket['invested'] += item['cost']
    bucket['has_estimate'] = bucket['has_estimate'] or item['estimated']
    if state == SETTLED:
        bucket['settled_invested'] += item['cost']
        bucket['returned'] += item['payout'] or Decimal(0)
        bucket['settled_count'] += 1
    else:
        bucket['pending_count'] += 1


def _tone(pnl: Decimal, settled_count: int, unpriced_count: int, pending_count: int) -> str:
    # 盈亏只反映能严格核实的部分；其余用独立语气词表达，不用 profit/loss 掩盖未知金额。
    if settled_count:
        return 'profit' if pnl > 0 else 'loss' if pnl < 0 else 'flat'
    if unpriced_count:
        return 'unpriced'
    return 'pending' if pending_count else 'empty'


def _public(bucket: dict) -> dict:
    # 盈亏只来自已结算且已核实盈亏的部分：返奖 − 已结算投入。
    pnl = bucket['returned'] - bucket['settled_invested']
    return {
        'invested': _money(bucket['invested']),
        'settled_invested': _money(bucket['settled_invested']),
        'returned': _money(bucket['returned']),
        'pnl': _money(pnl),
        'tone': _tone(pnl, bucket['settled_count'], bucket['unpriced_count'], bucket['pending_count']),
        'settled_count': bucket['settled_count'],
        'unpriced_count': bucket['unpriced_count'],
        'pending_count': bucket['pending_count'],
        'has_estimate': bucket['has_estimate'],
    }


def build_month(items: list[dict], first: date, following: date) -> list[dict]:
    """把条目按归日分桶，返回当月每一天（包含无投注的空日）。"""
    totals, by_sport, bets_by_day = {}, {}, {}
    for item in items:
        totals.setdefault(item['day'], _bucket())
        _add(totals[item['day']], item)
        sport_bucket = by_sport.setdefault(item['day'], {}).setdefault(item['sport'], _bucket())
        _add(sport_bucket, item)
        if bet := item.get('bet'):
            groups = bets_by_day.setdefault(item['day'], {'won': [], 'lost': []})
            group = 'won' if bet['won'] else 'lost'
            groups[group].append({
                'id': bet['id'],
                'kind': item['kind'],
                'legs': bet['legs'],
            })

    days, cursor = [], first
    while cursor < following:
        bucket = totals.get(cursor)
        if bucket is None:
            days.append(
                {
                    'day': cursor.isoformat(),
                    'empty': True,
                    'invested': '0',
                    'settled_invested': '0',
                    'returned': '0',
                    'pnl': '0',
                    'tone': 'empty',
                    'settled_count': 0,
                    'unpriced_count': 0,
                    'pending_count': 0,
                    'has_estimate': False,
                    'by_sport': [],
                }
            )
        else:
            entry = {'day': cursor.isoformat(), 'empty': False, **_public(bucket)}
            entry['bets'] = bets_by_day.get(cursor, {'won': [], 'lost': []})
            entry['by_sport'] = [
                {'sport': sport, 'name': SPORT_NAMES[sport], **_public(by_sport[cursor][sport])}
                for sport in SPORT_ORDER
                if sport in by_sport.get(cursor, {})
            ]
            days.append(entry)
        cursor += timedelta(days=1)
    return days


def calendar(db: Session, *, month: str | None = None, start=None, end=None) -> dict:
    """按月份或显式区间汇总每日盈亏。"""
    if month:
        first, following, window_start, window_end = month_bounds(month)
        days = (following - first).days
    else:
        if start is None or end is None or end <= start:
            raise CalendarError('时间范围无效')
        window_start, window_end = start.astimezone(TZ8), end.astimezone(TZ8)
        first, following = calendar_day(window_start), calendar_day(window_end) + timedelta(days=1)
        days = (following - first).days

    items = collect_items(db, window_start, window_end)
    summary = _bucket()
    for item in items:
        _add(summary, item)
    return {
        'month': month or first.strftime('%Y-%m'),
        'first_day': first.isoformat(),
        'last_day': (following - timedelta(days=1)).isoformat(),
        'days': build_month(items, first, following),
        'summary': _public(summary),
        'day_count': days,
    }
