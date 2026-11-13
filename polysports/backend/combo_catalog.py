"""校验官方 Combo 市场目录。目录数据只在本次检查中使用。"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict

from polymarket import PublicClient

from .trading_gateway import TradingError

_catalog_lock = threading.Lock()
_catalog_snapshot = None
_catalog_observed = 0.0


def resolve_positions(markets, outcomes, pages):
    """以 condition ID、市场 ID 与选项索引和标签关联目录，不使用 CLOB 标识。"""
    if len(markets) != len(outcomes) or len({outcome.id for outcome in outcomes}) != len(outcomes):
        raise TradingError('combo_legs_duplicate', '组合腿重复或不完整')
    by_condition = defaultdict(list)
    for n, page in enumerate(pages):
        if n >= 100:
            raise TradingError('combo_catalog_incomplete', '组合市场目录页数超限，无法验证')
        for entry in page.items:
            by_condition[str(entry.condition_id).lower()].append(entry)
        if getattr(page, 'limit_reached', False):
            raise TradingError('combo_catalog_incomplete', '组合市场目录不完整')
    result = {}
    for market, outcome in zip(markets, outcomes, strict=True):
        matches = by_condition[market.condition_id.lower()]
        if len(matches) != 1:
            raise TradingError('combo_market_unavailable', '组合市场不存在或目录身份不唯一')
        entry = matches[0]
        if str(entry.id) != str(market.gamma_market_id):
            raise TradingError('combo_market_mismatch', '组合目录与赛事市场身份不一致')
        choices = (entry.outcomes.yes, entry.outcomes.no)
        if outcome.outcome_index not in (0, 1):
            raise TradingError('combo_outcome_unsupported', '组合市场只支持已核对的二元选项')
        selected = choices[outcome.outcome_index]
        if selected.label.strip() != outcome.label.strip():
            raise TradingError('combo_outcome_mismatch', '组合目录选项与赛事市场不一致')
        ident = str(selected.position_id)
        if not re.fullmatch(r'(0|[1-9][0-9]{0,77})', ident) or int(ident) >= 2**256:
            raise TradingError('combo_position_invalid', '组合腿标识无效')
        result[outcome.id] = ident
    if len(set(result.values())) != len(result):
        raise TradingError('combo_legs_duplicate', '组合腿重复')
    return result


def fetch_positions(markets, outcomes):
    global _catalog_snapshot, _catalog_observed
    try:
        with _catalog_lock:
            now=time.monotonic()
            if _catalog_snapshot is None or now-_catalog_observed>5:
                pages=[]
                with PublicClient() as client:
                    for index,page in enumerate(client.list_combo_markets(page_size=100)):
                        if index>=100 or page.limit_reached:raise TradingError('combo_catalog_incomplete','组合市场目录不完整')
                        pages.append(page)
                _catalog_snapshot=tuple(pages)
                _catalog_observed=time.monotonic()
            return resolve_positions(markets,outcomes,_catalog_snapshot)
    except TradingError:
        raise
    except Exception as exc:
        raise TradingError('combo_catalog_unavailable', '无法验证组合市场目录') from exc
