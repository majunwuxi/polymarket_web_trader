"""平仓准入判据：赛果确认与市场停单都禁止平仓，未知状态不推断。"""
from types import SimpleNamespace as S

import pytest

from backend.close_policy import (MARKET_CLOSED_REASON, RESULT_CONFIRMED_REASON,
                                  assert_close_allowed, close_decision, game_result_state)
from backend.trading_gateway import TradingError


@pytest.mark.parametrize('score_verified,accepting_orders,blocked,reason', [
    (True, True, True, RESULT_CONFIRMED_REASON),
    (True, False, True, RESULT_CONFIRMED_REASON),
    (False, False, True, MARKET_CLOSED_REASON),
    (False, True, False, None),
    (False, None, False, None),  # 未知接单状态不得当作已结算
])
def test_close_decision(score_verified, accepting_orders, blocked, reason):
    assert close_decision(score_verified=score_verified, accepting_orders=accepting_orders) == (blocked, reason)


def test_game_result_state_requires_bound_binding_and_scores():
    class Db:
        def __init__(self, binding): self.binding = binding
        def scalar(self, query): return self.binding

    game = S(id='g', status='finished', home_score=100, away_score=90)
    assert game_result_state(Db(S(state='bound')), game) == (True, True)
    assert game_result_state(Db(None), game) == (True, False)
    assert game_result_state(Db(S(state='pending')), game) == (True, False)
    unfinished = S(id='g', status='scheduled', home_score=None, away_score=None)
    assert game_result_state(Db(S(state='bound')), unfinished) == (False, False)
    assert game_result_state(Db(S(state='bound')), None) == (False, False)


def test_assert_close_allowed_rejects_settled_and_market_closed():
    class Db:
        def scalar(self, query): return S(state='bound')

    game = S(id='g', status='finished', home_score=100, away_score=90)
    with pytest.raises(TradingError, match='不能平仓'):
        assert_close_allowed(Db(), game)
    with pytest.raises(TradingError, match='不能平仓'):
        assert_close_allowed(Db(), None, S(accepting_orders=False))
    assert assert_close_allowed(Db(), None, S(accepting_orders=True)) is None
