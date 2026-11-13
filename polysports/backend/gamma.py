from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from .sports_sources import SourceError, timestamp


@dataclass(frozen=True)
class Outcome:
    index: int
    label: str
    asset_id: str
    kind: str


@dataclass(frozen=True)
class GammaMarket:
    gamma_id: str
    condition_id: str
    version: str | None
    slug: str | None
    question: str | None
    accepting_orders: bool | None
    outcomes: tuple[Outcome,...]
    fingerprint: str


@dataclass(frozen=True)
class GammaEvent:
    gamma_id: str
    sport: str
    starts_at: datetime
    home: str
    away: str
    title: str | None
    slug: str | None
    markets: tuple[GammaMarket,...]


def normal(value: str) -> str:
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in unicodedata.normalize('NFKC',value).casefold()).split())


def names_match(source: list[str], external: str) -> bool:
    other=normal(external)
    if len(other)<4: return False
    for value in source:
        item=normal(value)
        if item==other or item.endswith(' '+other) or other.endswith(' '+item): return True
    return False


def parse_array(value: Any) -> list:
    if isinstance(value,str):
        try: value=json.loads(value)
        except (TypeError,ValueError) as exc: raise SourceError('invalid_market_array') from exc
    if not isinstance(value,list): raise SourceError('invalid_market_array')
    return value


def parse_market(raw: dict) -> GammaMarket:
    ident=raw.get('id'); condition=raw.get('conditionId')
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9]{1,255}',ident): raise SourceError('invalid_gamma_market_id')
    if not isinstance(condition,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',condition): raise SourceError('invalid_condition_id')
    version=raw.get('version'); version=version if version in ('v1','v2') else None
    accept=raw.get('acceptingOrders'); accept=accept if type(accept) is bool else None
    outcomes=[]
    if version:
        labels=parse_array(raw.get('outcomes'))
        ids=parse_array(raw.get('clobTokenIds') if version=='v1' else raw.get('positionIds'))
        if not labels or len(labels)!=len(ids) or len(labels)>32: raise SourceError('invalid_outcomes')
        for index,(label,asset) in enumerate(zip(labels,ids)):
            if not isinstance(label,str) or not label.strip() or len(label)>300: raise SourceError('invalid_outcomes')
            if not isinstance(asset,str) or not re.fullmatch(r'(0|[1-9][0-9]{0,77})',asset) or int(asset)>=2**256: raise SourceError('invalid_asset_id')
            outcomes.append(Outcome(index,label.strip(),asset,'ctf_token' if version=='v1' else 'position'))
        if len({x.asset_id for x in outcomes}) != len(outcomes): raise SourceError('duplicate_asset_id')
    content={'condition':condition.lower(),'version':version,'accept':accept,'outcomes':[(x.label,x.asset_id,x.kind) for x in outcomes]}
    fingerprint=hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return GammaMarket(ident,condition.lower(),version,raw.get('slug') if isinstance(raw.get('slug'),str) else None,raw.get('question') if isinstance(raw.get('question'),str) else None,accept,tuple(outcomes),fingerprint)


def parse_event(raw: Any, sport: str, start: datetime, home_names: list[str], away_names: list[str]) -> tuple[GammaEvent,bool] | None:
    if not isinstance(raw,dict): raise SourceError('invalid_event')
    tags=raw.get('tags')
    if not isinstance(tags,list): return None
    slugs={x.get('slug') for x in tags if isinstance(x,dict)}
    if ('nba' if sport=='nba' else 'soccer') not in slugs: return None
    teams=raw.get('teams')
    if not isinstance(teams,list) or len(teams)!=2 or not all(isinstance(x,dict) for x in teams): return None
    home=next((x for x in teams if x.get('ordering')=='home'),None)
    away=next((x for x in teams if x.get('ordering')=='away'),None)
    if not home or not away or not isinstance(home.get('name'),str) or not isinstance(away.get('name'),str): return None
    try: event_start=timestamp(raw.get('startTime'))
    except SourceError: return None
    if abs((event_start-start).total_seconds())>7200: return None
    ordered=names_match(home_names,home['name']) and names_match(away_names,away['name'])
    reversed_order=names_match(home_names,away['name']) and names_match(away_names,home['name'])
    if not ordered and not reversed_order: return None
    ident=raw.get('id')
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9]{1,255}',ident): raise SourceError('invalid_event_id')
    rows=raw.get('markets')
    if not isinstance(rows,list): raise SourceError('invalid_event_markets')
    markets=[]
    for market in rows:
        if not isinstance(market,dict): raise SourceError('invalid_event_markets')
        game_time=market.get('gameStartTime')
        if game_time:
            try:
                if abs((timestamp(game_time)-event_start).total_seconds())>60: raise SourceError('inconsistent_game_time')
            except SourceError: raise SourceError('inconsistent_game_time')
        markets.append(parse_market(market))
    return GammaEvent(ident,sport,event_start,home['name'],away['name'],raw.get('title') if isinstance(raw.get('title'),str) else None,raw.get('slug') if isinstance(raw.get('slug'),str) else None,tuple(markets)),bool(ordered and not reversed_order)


def discover(client: httpx.Client, sport: str, start: datetime, home_names: list[str], away_names: list[str], *, page_size: int=100, max_pages: int=100) -> list[tuple[GammaEvent,bool]]:
    if sport not in ('football','nba') or start.tzinfo is None: raise ValueError('invalid game')
    found=[]; seen=set(); cursors=set(); cursor=None
    params={'tag_slug':'nba' if sport=='nba' else 'soccer','closed':'false','limit':page_size,'start_time_min':(start-timedelta(hours=2)).isoformat(),'start_time_max':(start+timedelta(hours=2)).isoformat()}
    for _ in range(max_pages):
        if cursor: params['after_cursor']=cursor
        try:
            response=client.get('https://gamma-api.polymarket.com/events/keyset',params=params,timeout=20.0,follow_redirects=False)
            if response.status_code!=200: raise SourceError('http_'+str(response.status_code))
            body=response.json()
        except httpx.TimeoutException as exc: raise SourceError('timeout') from exc
        except (httpx.RequestError,ValueError) as exc: raise SourceError('network_or_invalid_json') from exc
        if not isinstance(body,dict) or 'next_cursor' not in body: raise SourceError('invalid_pagination')
        rows=body.get('events'); next_cursor=body.get('next_cursor')
        if not isinstance(rows,list) or len(rows)>page_size or (next_cursor is not None and not isinstance(next_cursor,str)): raise SourceError('invalid_pagination')
        for raw in rows:
            if not isinstance(raw,dict) or not isinstance(raw.get('id'),str): raise SourceError('invalid_event')
            if raw['id'] in seen: raise SourceError('pagination_duplicate')
            seen.add(raw['id'])
            matched=parse_event(raw,sport,start,home_names,away_names)
            if matched: found.append(matched)
        if not next_cursor: return found
        if next_cursor in cursors or not rows: raise SourceError('pagination_loop')
        cursors.add(next_cursor); cursor=next_cursor
    raise SourceError('pagination_limit')
