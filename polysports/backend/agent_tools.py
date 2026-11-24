"""Local stdio MCP with public market and portfolio reads only."""

import os
import re

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

server = FastMCP('PolySports market data')
read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
address_pattern = re.compile(r'0x[0-9a-fA-F]{40}')


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
