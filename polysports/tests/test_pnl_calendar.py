"""每日盈亏日历：归日边界与分桶口径（纯函数，不访问数据库）。"""
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from backend.pnl_calendar import (
    PENDING,
    SETTLED,
    TZ8,
    UNPRICED,
    CalendarError,
    build_month,
    calendar_day,
    day_bounds,
    month_bounds,
)


def at(year, month, day, hour, minute=0, second=0, tz=TZ8):
    return datetime(year, month, day, hour, minute, second, tzinfo=tz)


def test_calendar_day_starts_at_noon_beijing():
    # 北京时间 11:59:59 仍算前一天；12:00:00 才算当天。
    assert calendar_day(at(2026, 11, 1, 11, 59, 59)) == date(2026, 10, 31)
    assert calendar_day(at(2026, 11, 1, 12, 0, 0)) == date(2026, 11, 1)
    assert calendar_day(at(2026, 11, 1, 23, 59, 59)) == date(2026, 11, 1)
    # 次日 11:40 与当日 12:30 落在同一格。
    assert calendar_day(at(2026, 11, 1, 12, 30)) == calendar_day(at(2026, 11, 2, 11, 40))


def test_calendar_day_accepts_utc_input():
    # 04:30 UTC = 12:30 北京时间，属于当天。
    assert calendar_day(at(2026, 11, 1, 4, 30, tz=UTC)) == date(2026, 11, 1)
    # 03:59 UTC = 11:59 北京时间，属于前一天。
    assert calendar_day(at(2026, 11, 1, 3, 59, tz=UTC)) == date(2026, 10, 31)


def test_calendar_day_crosses_month_and_year():
    assert calendar_day(at(2027, 1, 1, 8, 0)) == date(2026, 12, 31)
    assert calendar_day(at(2027, 1, 1, 12, 0)) == date(2027, 1, 1)
    assert calendar_day(at(2026, 3, 1, 11, 0)) == date(2026, 2, 28)


def test_day_bounds_matches_calendar_day():
    for moment in (at(2026, 11, 1, 11, 59, 59), at(2026, 11, 1, 12, 0), at(2026, 11, 15, 3, 7)):
        day = calendar_day(moment)
        start, end = day_bounds(day)
        assert start <= moment < end
        assert start.astimezone(TZ8).hour == 12
        assert (end - start) == timedelta(days=1)
        assert calendar_day(start) == day
        assert calendar_day(end) == day + timedelta(days=1)


def test_month_bounds_parses_and_rejects():
    first, following, start, end = month_bounds('2026-11')
    assert first == date(2026, 11, 1) and following == date(2026, 12, 1)
    assert start.astimezone(TZ8) == at(2026, 11, 1, 12, 0)
    assert end.astimezone(TZ8) == at(2026, 12, 1, 12, 0)
    assert month_bounds('2026-12')[1] == date(2027, 1, 1)
    for bad in ('2026', '2026-13', 'abc', ''):
        with pytest.raises(CalendarError):
            month_bounds(bad)


def item(day, sport, cost, payout=None, state=SETTLED, estimated=False):
    return {
        'day': day,
        'sport': sport,
        'state': state,
        'cost': Decimal(cost),
        'payout': Decimal(payout) if payout is not None else None,
        'estimated': estimated,
    }


def test_build_month_covers_every_day_and_merges_same_day():
    day = date(2026, 11, 1)
    items = [item(day, 'football', '100', '133'), item(day, 'nba', '36', state=PENDING)]
    days = build_month(items, date(2026, 11, 1), date(2026, 11, 4))
    assert [d['day'] for d in days] == ['2026-11-01', '2026-11-02', '2026-11-03']
    first = days[0]
    assert first['empty'] is False
    # 投入含未结算；返奖与盈亏只看已结算部分。
    assert first['invested'] == '136'
    assert first['settled_invested'] == '100'
    assert first['returned'] == '133'
    assert first['pnl'] == '33'
    assert first['tone'] == 'profit'
    assert first['settled_count'] == 1
    assert first['pending_count'] == 1
    assert first['unpriced_count'] == 0
    # 空日不计盈亏，也不给出 profit/loss 语气。
    assert days[1] == {
        'day': '2026-11-02', 'empty': True, 'invested': '0', 'settled_invested': '0',
        'unpriced_invested': '0', 'returned': '0', 'pnl': '0', 'tone': 'empty', 'settled_count': 0,
        'unpriced_count': 0, 'pending_count': 0, 'has_estimate': False, 'by_sport': [],
    }


def test_build_month_splits_by_sport_without_double_counting():
    day = date(2026, 11, 5)
    items = [item(day, 'football', '50', '20'), item(day, 'nba', '30', '60'),
             item(day, 'mixed', '10', '0')]
    entry = build_month(items, day, date(2026, 11, 6))[0]
    rows = {row['sport']: row for row in entry['by_sport']}
    assert set(rows) == {'football', 'nba', 'mixed'}
    assert rows['football']['pnl'] == '-30'
    assert rows['football']['tone'] == 'loss'
    assert rows['nba']['pnl'] == '30'
    assert rows['nba']['tone'] == 'profit'
    assert rows['mixed']['pnl'] == '-10'
    # 合计等于三行之和，跨运动没有重复计入足球或篮球。
    assert entry['invested'] == '90'
    assert entry['pnl'] == '-10'
    assert entry['by_sport'][0]['sport'] == 'football'


def test_build_month_pending_day_has_no_amount_tone():
    day = date(2026, 11, 7)
    entry = build_month(
        [item(day, 'football', '20', state=PENDING, estimated=True)], day, date(2026, 11, 8)
    )[0]
    assert entry['invested'] == '20'
    assert entry['returned'] == '0'
    assert entry['pnl'] == '0'
    assert entry['tone'] == 'pending'
    assert entry['pending_count'] == 1
    assert entry['has_estimate'] is True


def test_build_month_unpriced_day_is_settled_but_excluded_from_pnl():
    """赛果已确认但盘口口径无法推断：标为已结算、盈亏待核实，不计入返奖与盈亏合计。"""
    day = date(2026, 11, 14)
    entry = build_month(
        [item(day, 'football', '6.4974', state=UNPRICED, estimated=True)], day, date(2026, 11, 15)
    )[0]
    assert entry['invested'] == '6.4974'
    # 盈亏待核实的投入单独统计，绝不并入已结算投入，否则盈亏会被算成全额亏损。
    assert entry['unpriced_invested'] == '6.4974'
    assert entry['settled_invested'] == '0'
    assert entry['returned'] == '0'
    assert entry['pnl'] == '0'
    assert entry['settled_count'] == 0
    assert entry['unpriced_count'] == 1
    assert entry['pending_count'] == 0
    # 不能因为盈亏待核实就标成待结算，也不能凭空给 profit/loss。
    assert entry['tone'] == 'unpriced'
    rows = {row['sport']: row for row in entry['by_sport']}
    assert rows['football']['tone'] == 'unpriced'
    assert rows['football']['unpriced_count'] == 1


def test_build_month_unpriced_alongside_settled_keeps_amount_tone():
    day = date(2026, 11, 14)
    items = [
        item(day, 'football', '10', '15'),
        item(day, 'football', '6', state=UNPRICED),
    ]
    entry = build_month(items, day, date(2026, 11, 15))[0]
    assert entry['settled_count'] == 1
    assert entry['unpriced_count'] == 1
    assert entry['pnl'] == '5'
    assert entry['tone'] == 'profit'


def test_build_month_flat_when_settled_break_even():
    day = date(2026, 11, 9)
    entry = build_month([item(day, 'nba', '25', '25')], day, date(2026, 11, 10))[0]
    assert entry['pnl'] == '0'
    assert entry['tone'] == 'flat'
