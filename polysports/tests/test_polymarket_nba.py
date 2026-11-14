"""所有比赛与响应均为模拟数据，不代表真实赛程。"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.polymarket_nba import discover_nba, nba_candidate
from backend.sports_sources import SourceError

START=datetime(2026,1,1,tzinfo=timezone.utc)


def event(ident='1',game='10'):
    return {'id':ident,'gameId':game,'active':True,'closed':False,'startTime':(START+timedelta(hours=2)).isoformat(),
            'tags':[{'slug':'nba'}],'teams':[{'id':'1','name':'Mock Home','ordering':'home','league':'nba'},
            {'id':'2','name':'Mock Away','ordering':'away','league':'nba'}]}


def test_candidate_requires_complete_single_game_identity():
    assert nba_candidate(event(),START,START+timedelta(hours=36)).game_id=='10'
    for field in ('gameId','startTime','teams'):
        raw=event();raw[field]=None
        assert nba_candidate(raw,START,START+timedelta(hours=36)) is None
    raw=event();raw['teams'][0]['league']='other'
    assert nba_candidate(raw,START,START+timedelta(hours=36)) is None
    raw=event();raw['startTime']=(START+timedelta(hours=36)).isoformat()
    assert nba_candidate(raw,START,START+timedelta(hours=36)) is None


def test_pagination_and_multiple_events_for_game():
    calls=[]
    def handler(req):
        calls.append(req)
        if len(calls)==1:return httpx.Response(200,json={'events':[event('1')],'next_cursor':'next'})
        return httpx.Response(200,json={'events':[event('2')]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        games,pages,count=discover_nba(client,START,page_size=2)
    assert pages==2 and count==2 and games[0].event_ids==('1','2')
    assert calls[1].url.params['after_cursor']=='next'


def test_conflicts_and_incomplete_pages_fail():
    def conflicting(req):
        second=event('2');second['teams'][0]['name']='Other Mock'
        return httpx.Response(200,json={'events':[event(),second]})
    with httpx.Client(transport=httpx.MockTransport(conflicting)) as client:
        with pytest.raises(SourceError,match='conflicting_game'): discover_nba(client,START,page_size=3)
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'events':[event()]}))) as client:
        with pytest.raises(SourceError,match='invalid_pagination'): discover_nba(client,START,page_size=1)
