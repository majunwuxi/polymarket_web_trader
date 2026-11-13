from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class SourceError(Exception):
    """来源失败：不包含响应内容或认证信息。"""

    def __init__(self, code: str, retry_after_seconds: int = 0):
        self.code = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)


def retry_delay(response: httpx.Response) -> int:
    """仅接受有限的非负秒数；不记录上游响应或请求凭据。"""
    value=response.headers.get('retry-after','')
    if value.isdecimal(): return int(value)
    if value:
        try:
            from email.utils import parsedate_to_datetime
            target=parsedate_to_datetime(value)
            if target.tzinfo is not None:
                return max(0,int((target-datetime.now(timezone.utc)).total_seconds())+1)
        except (TypeError,ValueError,OverflowError): pass
    return 0


@dataclass(frozen=True)
class SourceTeam:
    external_id: str
    name: str
    logo_url: str | None


@dataclass(frozen=True)
class SourceGame:
    provider: str
    sport: str
    external_id: str
    starts_at: datetime
    status: str
    provider_status: str
    home: SourceTeam
    away: SourceTeam
    league_id: str | None
    league_name: str | None
    league_logo: str | None
    home_score: int | None
    away_score: int | None


def timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise SourceError('invalid_datetime')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise SourceError('invalid_datetime') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceError('invalid_datetime')
    return parsed.astimezone(timezone.utc)


def identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
        raise SourceError('invalid_identity')
    return str(value)


def score(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SourceError('invalid_score')
    return value


def team(item: Any, nba: bool = False) -> SourceTeam:
    if not isinstance(item, dict):
        raise SourceError('invalid_team')
    name = item.get('full_name' if nba else 'name')
    if not isinstance(name, str) or not name.strip():
        raise SourceError('invalid_team')
    logo = None if nba else item.get('logo')
    if logo is not None and (not isinstance(logo, str) or not logo.startswith('https://')):
        logo = None
    return SourceTeam(identity(item.get('id')), name.strip(), logo)


def football_game(item: Any) -> SourceGame:
    try:
        fixture, teams, league = item['fixture'], item['teams'], item['league']
        raw = fixture['status']['short']
        if not isinstance(raw, str) or not raw:
            raise SourceError('invalid_status')
        status = {'NS':'scheduled', 'TBD':'unknown', '1H':'live', 'HT':'live', '2H':'live', 'ET':'live', 'BT':'live', 'P':'live', 'FT':'finished', 'AET':'finished', 'PEN':'finished', 'PST':'postponed', 'CANC':'cancelled', 'ABD':'cancelled', 'SUSP':'interrupted', 'INT':'interrupted'}.get(raw, 'unknown')
        goals = item['goals']
        home, away = team(teams['home']), team(teams['away'])
        if home.external_id == away.external_id:
            raise SourceError('invalid_team')
        name = league['name']
        if not isinstance(name, str) or not name.strip():
            raise SourceError('invalid_league')
        logo = league.get('logo')
        return SourceGame('api_football','football',identity(fixture['id']),timestamp(fixture['date']),status,raw,home,away,identity(league['id']),name,logo if isinstance(logo,str) and logo.startswith('https://') else None,score(goals['home']),score(goals['away']))
    except (KeyError, TypeError) as exc:
        raise SourceError('invalid_fixture') from exc


def nba_game(item: Any) -> SourceGame:
    try:
        raw = item.get('status_state', 'unknown')
        if not isinstance(raw, str):
            raise SourceError('invalid_status')
        status = {'scheduled':'scheduled','in_progress':'live','final':'finished','postponed':'postponed','canceled':'cancelled','delayed':'postponed','suspended':'interrupted','abandoned':'cancelled'}.get(raw,'unknown')
        home, away = team(item['home_team'], True), team(item['visitor_team'], True)
        if home.external_id == away.external_id:
            raise SourceError('invalid_team')
        # 赛前来源可能将未知比分表示为 0，不把它当成真实比分。
        hs = score(item.get('home_team_score')) if status in ('live','finished') else None
        aws = score(item.get('visitor_team_score')) if status in ('live','finished') else None
        return SourceGame('balldontlie','nba',identity(item['id']),timestamp(item['datetime']),status,raw,home,away,None,None,None,hs,aws)
    except (KeyError, TypeError) as exc:
        raise SourceError('invalid_fixture') from exc


def fetch_schedule(provider: str, key: str, start: datetime, client: httpx.Client, *, season_type: str | None = None) -> tuple[list[SourceGame], int, int]:
    if not key or key.startswith('CHANGE_ME'):
        raise SourceError('missing_api_key')
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError('start must be timezone-aware')
    start = start.astimezone(timezone.utc)
    end = start + timedelta(hours=36)
    if provider not in ('api_football','balldontlie'):
        raise ValueError('unknown provider')
    # NBA 的比赛日期可能按美国本地赛历划分，扩展一天再精确过滤 UTC 窗口。
    params = {'from':start.date().isoformat(),'to':end.date().isoformat(),'timezone':'UTC'} if provider == 'api_football' else {'start_date':(start-timedelta(days=1)).date().isoformat(),'end_date':(end+timedelta(days=1)).date().isoformat(),'per_page':100}
    if season_type is not None:
        if provider != 'balldontlie' or season_type != 'preseason':
            raise ValueError('unsupported season type')
        params['season_type'] = season_type
    url = 'https://v3.football.api-sports.io/fixtures' if provider == 'api_football' else 'https://api.balldontlie.io/v1/games'
    headers = {'x-apisports-key':key} if provider == 'api_football' else {'Authorization':key}
    seen, output, pages, received = set(), [], 0, 0
    football_total = None
    while True:
        try:
            response = client.get(url, params=params, headers=headers, timeout=20.0, follow_redirects=False)
            if response.status_code != 200:
                raise SourceError('http_' + str(response.status_code), retry_delay(response))
            body = response.json()
        except httpx.TimeoutException as exc:
            raise SourceError('timeout') from exc
        except (httpx.RequestError, ValueError) as exc:
            raise SourceError('network_or_invalid_json') from exc
        if not isinstance(body, dict):
            raise SourceError('invalid_response')
        pages += 1
        if pages > 1000:
            raise SourceError('pagination_limit')
        if provider == 'api_football':
            if body.get('errors') not in ([], {}, None):
                raise SourceError('provider_error')
            rows, paging = body.get('response'), body.get('paging')
            if not isinstance(rows,list) or not isinstance(paging,dict) or type(paging.get('current')) is not int or type(paging.get('total')) is not int or paging['current'] != pages or paging['total'] < pages:
                raise SourceError('invalid_pagination')
            if football_total is not None and football_total != paging['total']:
                raise SourceError('invalid_pagination')
            football_total = paging['total']
            if 'results' in body and (type(body['results']) is not int or body['results'] != len(rows)):
                raise SourceError('invalid_pagination')
            next_value = pages + 1 if pages < paging['total'] else None
        else:
            rows, meta = body.get('data'), body.get('meta')
            if not isinstance(rows,list) or not isinstance(meta,dict):
                raise SourceError('invalid_pagination')
            next_value = meta.get('next_cursor')
            if next_value is not None and (type(next_value) is not int or next_value <= 0):
                raise SourceError('invalid_pagination')
        received += len(rows)
        for row in rows:
            game = football_game(row) if provider == 'api_football' else nba_game(row)
            if start <= game.starts_at < end:
                output.append(game)
        if next_value is None:
            return output, pages, received
        if next_value in seen:
            raise SourceError('pagination_loop')
        seen.add(next_value)
        params['page' if provider == 'api_football' else 'cursor'] = next_value


def fetch_game(provider: str, key: str, external_id: str, client: httpx.Client) -> SourceGame:
    if not key or key.startswith('CHANGE_ME'):
        raise SourceError('missing_api_key')
    if not external_id.isdecimal():
        raise SourceError('invalid_identity')
    if provider == 'api_football':
        url='https://v3.football.api-sports.io/fixtures'
        params={'id':external_id,'timezone':'UTC'}
        headers={'x-apisports-key':key}
    elif provider == 'balldontlie':
        url='https://api.balldontlie.io/v1/games/'+external_id
        params={}
        headers={'Authorization':key}
    else:
        raise SourceError('invalid_provider')
    try:
        response=client.get(url,params=params,headers=headers,timeout=20.0,follow_redirects=False)
        if response.status_code != 200: raise SourceError('http_'+str(response.status_code), retry_delay(response))
        body=response.json()
    except httpx.TimeoutException as exc:
        raise SourceError('timeout') from exc
    except (httpx.RequestError,ValueError) as exc:
        raise SourceError('network_or_invalid_json') from exc
    if not isinstance(body,dict): raise SourceError('invalid_response')
    if provider=='api_football':
        if body.get('errors') not in ([],{},None): raise SourceError('provider_error')
        rows=body.get('response')
        if not isinstance(rows,list) or len(rows)!=1: raise SourceError('fixture_unavailable')
        result=football_game(rows[0])
    else:
        item=body.get('data')
        if not isinstance(item,dict): raise SourceError('fixture_unavailable')
        result=nba_game(item)
    if result.external_id != external_id: raise SourceError('fixture_mismatch')
    return result
