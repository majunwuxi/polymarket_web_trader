"""从 Polymarket 公开数据发现足球胜平负赛事，不执行交易。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .sports_sources import SourceError, retry_delay, timestamp

# Polymarket /sports 足球 primaryTagId；不包含同名其他运动、预选赛之外的杯赛或女足赛事。
FOOTBALL_COMPETITIONS = {
    '306':'英超', '102643':'英冠', '1494':'德甲', '102864':'德乙',
    '100618':'意甲', '780':'西甲', '102070':'法甲', '102871':'法乙',
    '101735':'荷甲', '105727':'荷乙', '101772':'葡超', '104930':'瑞超',
    '102651':'挪超', '105243':'芬超', '1234':'欧冠', '101787':'欧罗巴',
    '102763':'欧协联', '104937':'亚冠精英', '102232':'世界杯',
    '102544':'世预赛', '102540':'世预赛', '101680':'世预赛',
    '101280':'世预赛', '100787':'世预赛', '104327':'世预赛',
    '104342':'世预赛', '104316':'欧洲杯', '100816':'欧国联',
}
# 校验具体赛事系列，避免 AFC 等联盟通用标签误认成世预赛。
COMPETITION_SERIES = {
    '306':'10188', '102643':'10355', '1494':'10194', '102864':'10670',
    '100618':'10203', '780':'10193', '102070':'10195', '102871':'10675',
    '101735':'10286', '105727':'12353', '101772':'10330', '104930':'11637',
    '102651':'10362', '105243':'11864', '1234':'10204', '101787':'10209',
    '102763':'10437', '104937':'11644', '102232':'11433', '102544':'10243',
    '102540':'10240', '101680':'10241', '101280':'10244', '100787':'10246',
    '104327':'10294', '104342':'11463', '104316':'11430', '100816':'11446',
}
# 官方足球体育元数据当前没有可核实的亚洲杯 primaryTagId；不猜测。

@dataclass(frozen=True)
class FootballTeam:
    source_id: str
    name: str
    logo_url: str | None


@dataclass(frozen=True)
class FootballMarketGame:
    game_id: str
    starts_at: datetime
    home: FootballTeam
    away: FootballTeam
    event_ids: tuple[str, ...]
    market_ids: tuple[str, ...]
    competition_id: str
    competition_name: str


def _id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    result = str(value)
    return result if result.isascii() and result.isdecimal() and len(result) <= 255 else None


def _team(raw: Any) -> FootballTeam | None:
    if not isinstance(raw, dict): return None
    ident, name, logo = _id(raw.get('id')), raw.get('name'), raw.get('logo')
    if not ident or not isinstance(name, str) or not name.strip() or len(name) > 200: return None
    return FootballTeam(ident, name.strip(), logo if isinstance(logo, str) and logo.startswith('https://') else None)


def _candidate(raw: Any, start: datetime, end: datetime) -> FootballMarketGame | None:
    if not isinstance(raw, dict): raise SourceError('invalid_event')
    tags = raw.get('tags')
    if not isinstance(tags, list) or not any(isinstance(x, dict) and x.get('slug') == 'soccer' for x in tags): return None
    competition_ids = {_id(x.get('id')) for x in tags if isinstance(x, dict)} & FOOTBALL_COMPETITIONS.keys()
    if len(competition_ids) != 1: return None
    competition_id = next(iter(competition_ids))
    series = raw.get('series')
    if not isinstance(series, list) or COMPETITION_SERIES[competition_id] not in {_id(x.get('id')) for x in series if isinstance(x, dict)}:
        return None
    if raw.get('active') is not True or raw.get('closed') is not False: return None
    game_id, event_id = _id(raw.get('gameId')), _id(raw.get('id'))
    if not game_id or not event_id: return None
    try: starts_at = timestamp(raw.get('startTime'))
    except SourceError: return None
    if not start <= starts_at < end: return None
    teams = raw.get('teams')
    if not isinstance(teams, list) or len(teams) != 2: return None
    homes = [x for x in teams if isinstance(x, dict) and x.get('ordering') == 'home']
    aways = [x for x in teams if isinstance(x, dict) and x.get('ordering') == 'away']
    if len(homes) != 1 or len(aways) != 1: return None
    home, away = _team(homes[0]), _team(aways[0])
    if not home or not away or home.source_id == away.source_id: return None
    rows = raw.get('markets')
    if not isinstance(rows, list): raise SourceError('invalid_event_markets')
    market_ids = []
    for market in rows:
        if not isinstance(market, dict): raise SourceError('invalid_event_markets')
        if market.get('sportsMarketType') != 'moneyline' or market.get('active') is not True or market.get('closed') is not False: continue
        ident = _id(market.get('id'))
        if not ident: raise SourceError('invalid_gamma_market_id')
        try: market_start = timestamp(market.get('gameStartTime'))
        except SourceError: continue
        if market_start != starts_at: continue
        market_ids.append(ident)
    if not market_ids: return None
    if len(set(market_ids)) != len(market_ids): raise SourceError('duplicate_market_id')
    return FootballMarketGame(game_id, starts_at, home, away, (event_id,), tuple(sorted(market_ids)),
                              competition_id, FOOTBALL_COMPETITIONS[competition_id])


def discover_football(client: httpx.Client, start: datetime, *, page_size: int = 100, max_pages: int = 100) -> tuple[list[FootballMarketGame], int, int]:
    if start.tzinfo is None or start.utcoffset() is None: raise ValueError('start must be timezone-aware')
    if not 1 <= page_size <= 100 or max_pages < 1: raise ValueError('invalid pagination limits')
    start = start.astimezone(timezone.utc)
    end = start + timedelta(hours=36)
    params = {'tag_slug': 'soccer', 'closed': 'false', 'limit': page_size,
              'start_time_min': start.isoformat(), 'start_time_max': end.isoformat()}
    games: dict[str, FootballMarketGame] = {}
    event_ids: set[str] = set()
    cursors: set[str] = set()
    received = 0
    for page in range(1, max_pages + 1):
        try:
            response = client.get('https://gamma-api.polymarket.com/events/keyset', params=params, timeout=20.0, follow_redirects=False)
            if response.status_code != 200: raise SourceError('http_' + str(response.status_code), retry_delay(response))
            body = response.json()
        except httpx.TimeoutException as exc: raise SourceError('timeout') from exc
        except (httpx.RequestError, ValueError) as exc: raise SourceError('network_or_invalid_json') from exc
        if not isinstance(body, dict): raise SourceError('invalid_pagination')
        rows, cursor = body.get('events'), body.get('next_cursor')
        if not isinstance(rows, list) or len(rows) > page_size or (cursor is not None and not isinstance(cursor, str)):
            raise SourceError('invalid_pagination')
        if len(rows) == page_size and not cursor: raise SourceError('invalid_pagination')
        received += len(rows)
        for raw in rows:
            if not isinstance(raw, dict) or not _id(raw.get('id')): raise SourceError('invalid_event')
            event_id = _id(raw['id'])
            if event_id in event_ids: raise SourceError('pagination_duplicate')
            event_ids.add(event_id)
            candidate = _candidate(raw, start, end)
            if candidate is None: continue
            previous = games.get(candidate.game_id)
            if previous:
                if (previous.starts_at, previous.home, previous.away, previous.competition_id) != (candidate.starts_at, candidate.home, candidate.away, candidate.competition_id):
                    raise SourceError('conflicting_game')
                if set(previous.market_ids) & set(candidate.market_ids): raise SourceError('duplicate_market_id')
                candidate = FootballMarketGame(candidate.game_id, candidate.starts_at, candidate.home, candidate.away,
                                               tuple(sorted(previous.event_ids + candidate.event_ids)),
                                               tuple(sorted(previous.market_ids + candidate.market_ids)),
                                               candidate.competition_id, candidate.competition_name)
            games[candidate.game_id] = candidate
        if not cursor: return sorted(games.values(), key=lambda x: (x.starts_at, x.game_id)), page, received
        if cursor in cursors or not rows: raise SourceError('pagination_loop')
        cursors.add(cursor)
        params['after_cursor'] = cursor
    raise SourceError('pagination_limit')
