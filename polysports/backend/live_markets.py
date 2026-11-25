"""从 Gamma 读取比赛市场；不保存映射或执行交易。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

import httpx

from .gamma import normal
from .sports_sources import SourceError, retry_delay, timestamp

SUPPORTED_MARKET_TYPES = frozenset({'moneyline', 'spreads', 'totals'})
_EVENT_SLUG = re.compile(r'[A-Za-z0-9-]{1,250}')
_PARTIAL_MARKET = re.compile(r'\b(?:first|second|1st|2nd|1h|2h|half|quarter|period|q[1-4]|team\s+total)\b', re.IGNORECASE)


def supported_full_game_market(raw):
    if not isinstance(raw, dict):
        raise SourceError('invalid_market')
    if raw.get('sportsMarketType') not in SUPPORTED_MARKET_TYPES:
        return False
    # 类型是主要依据；文本明确指出半场、单队或局节时保守排除。
    for key in ('question', 'groupItemTitle', 'slug'):
        value = raw.get(key)
        if isinstance(value, str) and _PARTIAL_MARKET.search(value.replace('-', ' ')):
            return False
    return True


def _array(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def _price(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return str(number) if number.is_finite() and 0 <= number <= 1 else None


def _event_matches(raw, game_id, sport, starts_at, home, away, *, allow_missing_game_id=False):
    if not isinstance(raw, dict):
        raise SourceError('invalid_event')
    if game_id is not None and str(raw.get('gameId')) != game_id and not (
        allow_missing_game_id and raw.get('gameId') is None
    ):
        return False
    tags = raw.get('tags')
    if not isinstance(tags, list) or not any(
        isinstance(x, dict) and x.get('slug') == ('soccer' if sport == 'football' else 'nba') for x in tags
    ):
        return False
    teams = raw.get('teams')
    if not isinstance(teams, list) or len(teams) != 2:
        return False
    homes = [x for x in teams if isinstance(x, dict) and x.get('ordering') == 'home']
    aways = [x for x in teams if isinstance(x, dict) and x.get('ordering') == 'away']
    if len(homes) != 1 or len(aways) != 1:
        return False
    if not isinstance(homes[0].get('name'), str) or not isinstance(aways[0].get('name'), str):
        return False
    if normal(homes[0]['name']) != normal(home) or normal(aways[0]['name']) != normal(away):
        return False
    try:
        source_start = timestamp(raw.get('startTime'))
    except SourceError:
        return False
    return abs((source_start - starts_at).total_seconds()) <= (60 if game_id is not None else 7200)


def _market(raw):
    if not isinstance(raw, dict):
        raise SourceError('invalid_market')
    market_id = str(raw.get('id', ''))
    if not re.fullmatch(r'[0-9]{1,255}', market_id):
        raise SourceError('invalid_market')
    labels, prices = _array(raw.get('outcomes')), _array(raw.get('outcomePrices'))
    if len(labels) > 32 or len(prices) > 32:
        raise SourceError('invalid_market')
    outcomes = []
    for index, label in enumerate(labels):
        if not isinstance(label, str) or not label.strip() or len(label) > 300:
            raise SourceError('invalid_market')
        outcomes.append({'label': label.strip(), 'price': _price(prices[index]) if index < len(prices) else None})
    return {
        'id': market_id,
        'question': raw.get('question') if isinstance(raw.get('question'), str) else None,
        'group': raw.get('groupItemTitle') if isinstance(raw.get('groupItemTitle'), str) else None,
        'type': raw.get('sportsMarketType') if isinstance(raw.get('sportsMarketType'), str) else None,
        'accepting_orders': raw.get('acceptingOrders') if type(raw.get('acceptingOrders')) is bool else None,
        'closed': raw.get('closed') if type(raw.get('closed')) is bool else None,
        'outcomes': outcomes,
    }


def _event_markets(raw):
    markets = raw.get('markets')
    if not isinstance(markets, list):
        raise SourceError('invalid_event_markets')
    slug = raw.get('slug')
    return {
        'id': str(raw['id']),
        'title': raw.get('title') if isinstance(raw.get('title'), str) else None,
        'slug': slug if isinstance(slug, str) and _EVENT_SLUG.fullmatch(slug) else None,
        'markets': [_market(x) for x in markets if supported_full_game_market(x)],
    }


def _extra_football_events(client, events, game_id, starts_at, home, away, seen):
    slugs = {event['slug'] for event in events}
    market_ids = {market['id'] for event in events for market in event['markets']}
    for event in events:
        parent = event['slug']
        if not parent or parent.endswith('-more-markets') or not any(
            market['type'] == 'moneyline' for market in event['markets']
        ):
            continue
        slug = parent + '-more-markets'
        if slug in slugs or not _EVENT_SLUG.fullmatch(slug):
            continue
        slugs.add(slug)
        try:
            response = client.get(
                'https://gamma-api.polymarket.com/events/slug/' + slug, timeout=5, follow_redirects=False
            )
            if response.status_code != 200:
                continue
            raw = response.json()
        except (httpx.RequestError, ValueError):
            continue
        if (
            not isinstance(raw, dict)
            or raw.get('slug') != slug
            or raw.get('closed') is not False
            or not re.fullmatch(r'[0-9]{1,255}', str(raw.get('id', '')))
            or str(raw['id']) in seen
            or not _event_matches(raw, game_id, 'football', starts_at, home, away, allow_missing_game_id=True)
        ):
            continue
        try:
            extra = _event_markets(raw)
        except SourceError:
            continue
        extra['read_only'] = raw.get('gameId') is None
        extra['markets'] = [market for market in extra['markets'] if market['id'] not in market_ids]
        if extra['markets']:
            seen.add(extra['id'])
            market_ids.update(market['id'] for market in extra['markets'])
            yield extra


def fetch_live_markets(
    client: httpx.Client,
    *,
    source_id: str | None,
    sport: str,
    starts_at: datetime,
    home: str,
    away: str,
    page_size: int = 100,
    max_pages: int = 30,
):
    if sport not in ('football', 'nba') or starts_at.tzinfo is None:
        raise ValueError('invalid game')
    if source_id is not None and not re.fullmatch(r'[0-9]{1,255}', source_id):
        raise SourceError('invalid_game_id')
    params = {'closed': 'false', 'limit': page_size}
    if source_id is not None:
        params['game_id'] = source_id
    else:
        params.update(
            {
                'tag_slug': 'soccer' if sport == 'football' else 'nba',
                'start_time_min': (starts_at - timedelta(hours=2)).isoformat(),
                'start_time_max': (starts_at + timedelta(hours=2)).isoformat(),
            }
        )
    events = []
    seen = set()
    cursors = set()
    game_ids = set()
    for _ in range(max_pages):
        try:
            response = client.get(
                'https://gamma-api.polymarket.com/events/keyset', params=params, timeout=20, follow_redirects=False
            )
            if response.status_code != 200:
                raise SourceError('http_' + str(response.status_code), retry_delay(response))
            body = response.json()
        except httpx.TimeoutException as exc:
            raise SourceError('timeout') from exc
        except (httpx.RequestError, ValueError) as exc:
            raise SourceError('network_or_invalid_json') from exc
        if not isinstance(body, dict):
            raise SourceError('invalid_pagination')
        rows, cursor = body.get('events'), body.get('next_cursor')
        if (
            not isinstance(rows, list)
            or len(rows) > page_size
            or (cursor is not None and not isinstance(cursor, str))
            or (len(rows) == page_size and not cursor)
        ):
            raise SourceError('invalid_pagination')
        for raw in rows:
            if not isinstance(raw, dict) or not re.fullmatch(r'[0-9]{1,255}', str(raw.get('id', ''))):
                raise SourceError('invalid_event')
            event_id = str(raw['id'])
            if event_id in seen:
                raise SourceError('pagination_duplicate')
            seen.add(event_id)
            if not _event_matches(raw, source_id, sport, starts_at, home, away):
                continue
            game_id = str(raw.get('gameId', ''))
            if not re.fullmatch(r'[0-9]{1,255}', game_id):
                continue
            game_ids.add(game_id)
            event = _event_markets(raw)
            if event['markets']:
                events.append(event)
        if not cursor:
            if source_id is None and len(game_ids) > 1:
                raise SourceError('ambiguous_game')
            if source_id is not None and sport == 'football':
                events.extend(_extra_football_events(client, tuple(events), source_id, starts_at, home, away, seen))
            return {
                'events': events,
                'match_status': 'source_identity' if source_id else 'candidate',
                'observed_at': datetime.now(UTC).isoformat(),
            }
        if not rows or cursor in cursors:
            raise SourceError('pagination_loop')
        cursors.add(cursor)
        params['after_cursor'] = cursor
    raise SourceError('pagination_limit')
