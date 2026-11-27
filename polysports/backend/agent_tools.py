"""Local stdio MCP with public market and portfolio reads only."""

import os
import re
import asyncio
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import urlencode
from uuid import UUID
from typing import Literal

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

server = FastMCP('PolySports market data')
read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
address_pattern = re.compile(r'0x[0-9a-fA-F]{40}')
PROJECT_URL = 'https://example.invalid'


def project_read_token() -> str | None:
    name = 'POLYSPORTS_AGENT_READ_TOKEN'
    token = os.getenv(name)
    if token or os.name != 'nt':
        return token
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


async def project_read(path: str, params: dict | None = None) -> dict:
    token = project_read_token()
    if not token:
        raise ValueError('尚未配置 PolySports 只读令牌')
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.get(
            PROJECT_URL + '/api/v1' + path, params=params,
            headers={'Authorization': 'Bearer ' + token},
        )
    if not response.is_success:
        raise ValueError(f'PolySports 读取失败（HTTP {response.status_code}）')
    return response.json()['data']


@server.tool(annotations=read_only)
async def project_games(sport: Literal['football', 'nba'], start: str, end: str, cursor: str | None = None) -> dict:
    """List project games and their IDs for an ISO 8601 interval with explicit time zones."""
    params = {'sport': sport, 'from': start, 'to': end, 'limit': 100}
    if cursor:
        params['cursor'] = cursor
    return await project_read('/games', params)


@server.tool(annotations=read_only)
async def project_combo_options(game_id: UUID) -> dict:
    """Find confirmed, open combo options and their project IDs for a game."""
    data = await project_read(f'/games/{game_id}/markets')
    return {'items': [
        {
            'market_link_id': item['link']['id'],
            'question': market['question'],
            'outcomes': [
                {'outcome_id': outcome['id'], 'label': outcome['label']}
                for outcome in market['outcomes']
            ],
        }
        for item in data['items']
        if item['link']['state'] == 'confirmed'
        and (market := item['market'])['accepting_orders'] is True
        and market['combo_enabled'] is not False
    ]}


@server.tool(annotations=read_only)
async def project_wallets() -> dict:
    """List project wallet IDs and labels for creating a combo draft."""
    return await project_read('/wallets')


@server.tool(annotations=read_only)
async def project_wallet_budget(wallet_id: UUID) -> dict:
    """Read current collateral balance and shared daily buy allowance for this wallet."""
    wallet, limits = await asyncio.gather(
        project_read(f'/wallets/{wallet_id}/status'),
        project_read(f'/wallets/{wallet_id}/trade-limits'),
    )
    balance = wallet.get('collateral_balance')
    if balance is None or wallet.get('availability') != 'allowed':
        diagnostic = wallet.get('diagnostic') or {}
        raise ValueError(diagnostic.get('message') or '无法核实钱包可用余额')
    return {
        'wallet': wallet['wallet'],
        'wallet_available': str(balance),
        'daily_remaining': limits['remaining_daily_usd'],
        'resets_at': limits['resets_at'],
    }


class ConfirmationCheck(BaseModel):
    confirmed: bool = Field(description='确认完成这次无资金操作的界面测试')


@server.tool(annotations=read_only)
async def test_combo_confirmation(ctx: Context) -> dict:
    """Test the Codex confirmation interface without connecting to a wallet or submitting an order."""
    result = await ctx.elicit('测试逐单确认。此操作不会连接钱包或发送订单。', ConfirmationCheck)
    return {'action': result.action, 'confirmed': result.action == 'accept' and bool(result.data and result.data.confirmed)}


approval_test_runs = 0


@server.tool(annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False,
))
def test_combo_tool_approval() -> dict:
    """Increment a temporary in-memory counter to test Codex write-tool approval. No wallet or order is used."""
    global approval_test_runs
    approval_test_runs += 1
    return {'executed': True, 'test_runs': approval_test_runs, 'submitted_order': False}


@server.tool(annotations=read_only)
def combo_stake(wallet_available: str, daily_remaining: str | None = None) -> dict:
    """Calculate a proposed combo stake in pUSD from current wallet funds and today's remaining limit."""
    try:
        available = Decimal(wallet_available)
        remaining = Decimal(daily_remaining) if daily_remaining is not None else available
    except InvalidOperation as exc:
        raise ValueError('金额必须是十进制数字') from exc
    if any(not amount.is_finite() or amount < 0 for amount in (available, remaining)):
        raise ValueError('金额必须是有限的非负数')
    cap = min(available, remaining)
    if cap < 1:
        return {'status': 'skip', 'stake_pusd': None, 'reason': '可用资金或今日剩余额度不足 1 pUSD'}
    stake = min(cap, max(Decimal(1), available * Decimal('0.05')))
    return {'status': 'proposed', 'stake_pusd': format(stake.quantize(Decimal('0.000001'), rounding=ROUND_DOWN), 'f')}


class DraftLeg(BaseModel):
    market_link_id: UUID
    outcome_id: UUID


@server.tool(annotations=read_only)
async def combo_draft(wallet_id: UUID, legs: list[DraftLeg]) -> dict:
    """Prepare a PolySports draft using confirmed project IDs and live wallet limits. Does not send an order."""
    pairs = [f'{leg.market_link_id}:{leg.outcome_id}' for leg in legs]
    if not 2 <= len(pairs) <= 50 or len(set(pairs)) != len(pairs):
        raise ValueError('组合需要 2 至 50 个不同的选项')
    budget = await project_wallet_budget(wallet_id)
    result = combo_stake(budget['wallet_available'], budget['daily_remaining'])
    if result['status'] == 'proposed':
        options = await asyncio.gather(*(
            project_read(f'/combo-legs/{leg.market_link_id}/{leg.outcome_id}') for leg in legs
        ))
        if any(not option.get('accepting_orders') for option in options):
            raise ValueError('草稿包含已经停单的选项')
        query = urlencode({'wallet': str(wallet_id), 'stake': result['stake_pusd'], 'draft': ','.join(pairs)})
        result.update(
            url=PROJECT_URL + '/combos?' + query,
            wallet=budget['wallet'],
            legs=[{key: option[key] for key in ('market_question', 'outcome_label')} for option in options],
        )
    return result


async def read_public(url, params):
    async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


@server.tool(annotations=read_only)
async def polymarket_markets(limit: int = 10) -> dict:
    """Read a page of open Polymarket events with their market questions and prices."""
    if not 1 <= limit <= 25:
        raise ValueError('limit must be between 1 and 25')
    result = await read_public('https://gamma-api.polymarket.com/events/keyset', {'closed': 'false', 'limit': limit})
    return {
        'events': [
            {
                'title': event.get('title'),
                'slug': event.get('slug'),
                'markets': [
                    {key: market.get(key) for key in ('question', 'outcomes', 'outcomePrices', 'acceptingOrders')}
                    for market in (event.get('markets') or [])[:12]
                ],
            }
            for event in result['events']
        ],
    }


@server.tool(annotations=read_only)
async def polymarket_portfolio(wallet: str) -> dict:
    """Read public position valuation and the first 20 open positions for an allowed wallet."""
    allowed = {item.strip().lower() for item in os.getenv('POLYSPORTS_AGENT_WALLETS', '').split(',')}
    if not address_pattern.fullmatch(wallet) or wallet.lower() not in allowed:
        raise ValueError('Wallet is not configured for the read-only agent')
    base = 'https://data-api.polymarket.com/v2'
    valuation = await read_public(base + '/value', {'user': wallet})
    positions = await read_public(base + '/positions', {'user': wallet, 'limit': 20})
    return {
        'wallet': wallet,
        'position_value': valuation['data'],
        'open_positions': positions['data'],
        'has_more_positions': bool((positions.get('pagination') or {}).get('next_cursor')),
    }


if __name__ == '__main__':
    server.run(transport='stdio')
