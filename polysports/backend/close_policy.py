"""平仓准入：比赛结果已确认或市场已停单/结算时，不再允许发起卖出平仓。

赛事终态（外部赛果已绑定核实）与市场终态（停止接单、链上结算）是两个
独立信号；任一成立都说明持仓已无继续平仓的意义。判据集中在纯函数里，
列表接口用它给出只读展示字段，平仓链路用它做硬校验，避免只靠前端禁用。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Game, ResultBindingRequest
from .trading_gateway import TradingError

RESULT_CONFIRMED_REASON = '比赛结果已确认，持仓已进入结算，不能平仓'
MARKET_CLOSED_REASON = '市场已停止接单或进入结算，不能平仓'


def game_result_state(db: Session, game: Game | None):
    """返回 (比赛已结束, 外部赛果已绑定核实)，与结算核对接口口径一致。"""
    if game is None:
        return False, False
    binding = db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id == game.id))
    finished = game.status == 'finished'
    verified = bool(binding and binding.state == 'bound' and finished
                    and game.home_score is not None and game.away_score is not None)
    return finished, verified


def close_decision(*, score_verified: bool, accepting_orders):
    """返回 (是否禁止平仓, 原因)。None/未知的接单状态不推断为已结算。"""
    if score_verified:
        return True, RESULT_CONFIRMED_REASON
    if accepting_orders is False:
        return True, MARKET_CLOSED_REASON
    return False, None


def assert_close_allowed(db: Session, game: Game | None, market=None):
    """平仓链路统一硬校验；防止绕过界面直接调用预览/下单接口。"""
    _, verified = game_result_state(db, game)
    blocked, reason = close_decision(score_verified=verified,
                                     accepting_orders=getattr(market, 'accepting_orders', None))
    if blocked:
        raise TradingError('position_settled', reason)
