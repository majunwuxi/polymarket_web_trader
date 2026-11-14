"""发现 Polymarket 上可识别的 NBA 单场比赛；不读取或推断赛果。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from .polymarket_football import FootballTeam, _id, _team
from .sports_sources import SourceError, retry_delay, timestamp


@dataclass(frozen=True)
class NbaGame:
    game_id: str
    starts_at: datetime
    home: FootballTeam
    away: FootballTeam
    event_ids: tuple[str, ...]


def nba_candidate(raw, start, end):
    if not isinstance(raw, dict): raise SourceError('invalid_event')
    tags=raw.get('tags')
    if not isinstance(tags,list) or not any(isinstance(x,dict) and x.get('slug')=='nba' for x in tags): return None
    if raw.get('active') is not True or raw.get('closed') is not False: return None
    game_id,event_id=_id(raw.get('gameId')),_id(raw.get('id'))
    if not game_id or not event_id: return None
    try: starts_at=timestamp(raw.get('startTime'))
    except SourceError: return None
    if not start<=starts_at<end: return None
    teams=raw.get('teams')
    if not isinstance(teams,list) or len(teams)!=2: return None
    homes=[t for t in teams if isinstance(t,dict) and t.get('ordering')=='home' and t.get('league')=='nba']
    aways=[t for t in teams if isinstance(t,dict) and t.get('ordering')=='away' and t.get('league')=='nba']
    if len(homes)!=1 or len(aways)!=1: return None
    home,away=_team(homes[0]),_team(aways[0])
    if not home or not away or home.source_id==away.source_id: return None
    return NbaGame(game_id,starts_at,home,away,(event_id,))


def discover_nba(client: httpx.Client, start: datetime, *, page_size=100, max_pages=100):
    if start.tzinfo is None or start.utcoffset() is None: raise ValueError('start must be timezone-aware')
    if not 1<=page_size<=100 or max_pages<1: raise ValueError('invalid pagination limits')
    start=start.astimezone(timezone.utc); end=start+timedelta(hours=36)
    params={'tag_slug':'nba','closed':'false','limit':page_size,'start_time_min':start.isoformat(),'start_time_max':end.isoformat()}
    games={}; seen=set(); cursors=set(); received=0
    for page in range(1,max_pages+1):
        try:
            response=client.get('https://gamma-api.polymarket.com/events/keyset',params=params,timeout=20.0,follow_redirects=False)
            if response.status_code!=200: raise SourceError('http_'+str(response.status_code),retry_delay(response))
            body=response.json()
        except httpx.TimeoutException as exc: raise SourceError('timeout') from exc
        except (httpx.RequestError,ValueError) as exc: raise SourceError('network_or_invalid_json') from exc
        if not isinstance(body,dict): raise SourceError('invalid_pagination')
        rows,cursor=body.get('events'),body.get('next_cursor')
        if not isinstance(rows,list) or len(rows)>page_size or (cursor is not None and not isinstance(cursor,str)) or (len(rows)==page_size and not cursor): raise SourceError('invalid_pagination')
        received+=len(rows)
        for raw in rows:
            if not isinstance(raw,dict) or not _id(raw.get('id')): raise SourceError('invalid_event')
            event_id=_id(raw['id'])
            if event_id in seen: raise SourceError('pagination_duplicate')
            seen.add(event_id); item=nba_candidate(raw,start,end)
            if item is None: continue
            previous=games.get(item.game_id)
            if previous:
                if (previous.starts_at,previous.home,previous.away)!=(item.starts_at,item.home,item.away): raise SourceError('conflicting_game')
                item=NbaGame(item.game_id,item.starts_at,item.home,item.away,tuple(sorted(previous.event_ids+item.event_ids)))
            games[item.game_id]=item
        if not cursor: return sorted(games.values(),key=lambda x:(x.starts_at,x.game_id)),page,received
        if cursor in cursors or not rows: raise SourceError('pagination_loop')
        cursors.add(cursor);params['after_cursor']=cursor
    raise SourceError('pagination_limit')
