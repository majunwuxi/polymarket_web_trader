"""校验官方 Combo 市场目录。目录数据只在本次检查中使用。"""
from __future__ import annotations

import re
from collections import defaultdict

from polymarket import PublicClient

from .trading_gateway import TradingError

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


def resolve_market_details(markets, outcomes, details):
    """验证官方逐市场详情，使用该详情中的 Combo position ID。"""
    if len(markets)!=len(outcomes) or len(markets)!=len(details) or len({outcome.id for outcome in outcomes})!=len(outcomes):
        raise TradingError('combo_legs_duplicate','组合腿重复或不完整')
    result={}
    for market,outcome,detail in zip(markets,outcomes,details,strict=True):
        if str(detail.id)!=str(market.gamma_market_id) or str(detail.condition_id).lower()!=market.condition_id.lower():
            raise TradingError('combo_market_mismatch','组合市场身份不一致')
        if detail.state.combo_status!='enabled' or detail.state.active is not True or detail.state.closed is not False:
            raise TradingError('combo_market_unavailable','该市场未明确启用组合或已关闭')
        if outcome.outcome_index not in (0,1):
            raise TradingError('combo_outcome_unsupported','组合市场只支持已核对的二元选项')
        choices=(detail.outcomes.yes,detail.outcomes.no)
        ids=[str(choice.position_id) for choice in choices]
        if any(not re.fullmatch(r'(0|[1-9][0-9]{0,77})',x) or int(x)>=2**256 for x in ids) or len(set(ids))!=2:
            raise TradingError('combo_position_invalid','组合腿标识不完整或无效')
        selected=choices[outcome.outcome_index]
        if not isinstance(selected.label,str) or selected.label.strip()!=outcome.label.strip():
            raise TradingError('combo_outcome_mismatch','组合选项与赛事市场不一致')
        result[outcome.id]=ids[outcome.outcome_index]
    if len(set(result.values()))!=len(result): raise TradingError('combo_legs_duplicate','组合腿重复')
    return result


def fetch_positions(markets, outcomes):
    try:
        with PublicClient() as client:
            details_by_id={}
            for market in markets:
                if market.gamma_market_id not in details_by_id:
                    details_by_id[market.gamma_market_id]=client.get_market(id=market.gamma_market_id)
            return resolve_market_details(markets,outcomes,[details_by_id[market.gamma_market_id] for market in markets])
    except TradingError:
        raise
    except Exception as exc:
        raise TradingError('combo_catalog_unavailable', '无法验证组合市场目录') from exc
