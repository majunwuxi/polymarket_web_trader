"""交易记录隐藏规则的模拟测试：过滤必须落在 SQL、分页之前，不连接数据库。

覆盖两条口径：单场按本场开赛时间，组合按所有腿中最晚一场开赛时间；
「已结算」都用列表 close_blocked 的同一套信号（外部赛果已绑定核实 / 市场停单）。
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from backend import combo_api, trading_api
from backend.close_policy import SETTLED_VISIBILITY_HOURS, settled_visibility_cutoff
from backend.models import ComboRFQ, TradeIntent


def mock_request():
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(engine=object())),
        state=SimpleNamespace(request_id='mock'))


def capture(monkeypatch, module):
    """替换模块里的 Session，收集真正交给数据库的查询对象。"""
    seen = []

    class SessionMock:
        def __init__(self, engine): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def scalars(self, query): seen.append(query); return []

    monkeypatch.setattr(module, 'Session', SessionMock)
    return seen


def compiled(query):
    return query.compile(dialect=postgresql.dialect())


def test_cutoff_is_exactly_24_hours_before_now():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    assert SETTLED_VISIBILITY_HOURS == 24
    assert settled_visibility_cutoff(now) == now - timedelta(hours=24)
    assert settled_visibility_cutoff().tzinfo is not None


def test_intent_filter_uses_game_start_and_settled_signals_before_limit(monkeypatch):
    seen = capture(monkeypatch, trading_api)
    result = trading_api.list_intents(mock_request(), limit=1, start=None, end=None, hide_settled=True)
    assert result['data']['items'] == []
    sql = str(compiled(seen[0]))
    assert 'JOIN games' in sql and 'JOIN poly_markets' in sql
    assert 'games.starts_at <' in sql
    assert 'result_binding_requests.state' in sql
    assert 'poly_markets.accepting_orders IS false' in sql
    assert 'LIMIT' in sql


def test_intent_list_without_hide_settled_stays_unfiltered(monkeypatch):
    seen = capture(monkeypatch, trading_api)
    trading_api.list_intents(mock_request(), limit=1, start=None, end=None)
    assert 'WHERE' not in str(seen[0])
    assert 'JOIN games' not in str(seen[0])


def test_intent_filter_accepts_explicit_cutoff():
    cutoff = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    query = trading_api.hide_old_settled_intents(select(TradeIntent), cutoff)
    assert cutoff in compiled(query).params.values()


def test_combo_filter_uses_last_leg_start_not_first(monkeypatch):
    """「最后一场开赛超过 24 小时」= 不存在任何一条腿在阈值之后开赛。"""
    seen = capture(monkeypatch, combo_api)
    result = combo_api.list_combos(mock_request(), limit=1, cursor=None,
                                   state='confirmed', hide_settled=True)
    assert result['data']['items'] == []
    sql = str(compiled(seen[0]))
    assert 'games.starts_at >=' in sql          # 相关子查询找「还有腿没超 24 小时」
    assert 'NOT (EXISTS' in sql                 # 外层取反，等价于 max(starts_at) < cutoff
    assert 'combo_legs.rfq_id = combo_rfqs.id' in sql
    assert 'result_binding_requests.state' in sql
    assert 'poly_markets.accepting_orders IS false' in sql
    assert 'LIMIT' in sql
    params = compiled(seen[0]).params
    assert 'confirmed' in params.values()
    assert any(isinstance(v, datetime) for v in params.values())


def test_combo_filter_preserves_confirmed_and_game_filter_before_pagination(monkeypatch):
    from uuid import uuid4
    seen = capture(monkeypatch, combo_api)
    game_id = uuid4()
    combo_api.list_combos(mock_request(), limit=1, cursor=None,
                          state='confirmed', game_id=game_id, hide_settled=True)
    sql = str(compiled(seen[0]))
    params = compiled(seen[0]).params.values()
    assert 'LIMIT' in sql and sql.count('EXISTS') >= 3
    assert 'confirmed' in params and game_id in params


def test_combo_filter_accepts_explicit_cutoff():
    cutoff = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    query = combo_api.hide_old_settled_combos(select(ComboRFQ), cutoff)
    assert cutoff in compiled(query).params.values()


def test_combo_list_without_hide_settled_stays_unfiltered(monkeypatch):
    seen = capture(monkeypatch, combo_api)
    combo_api.list_combos(mock_request(), limit=1, cursor=None, state=None)
    sql = str(compiled(seen[0]))
    # 默认只隐藏已在外部平仓的组合；未请求 hide_settled 时不加结算/时间过滤。
    assert 'combo_rfqs.position_closed_at IS NULL' in sql
    assert 'EXISTS' not in sql
