"""所有市场响应为模拟数据，不发出真实交易。"""
from datetime import datetime, timezone

import httpx
import pytest

from backend.live_markets import fetch_live_markets, supported_full_game_market
from backend.sports_sources import SourceError

NOW=datetime(2026,1,1,12,tzinfo=timezone.utc)


def event(ident='1',game=100,sport='soccer'):
    return {'id':ident,'gameId':game,'startTime':NOW.isoformat(),'tags':[{'slug':sport}],
            'teams':[{'name':'Mock Home','ordering':'home'},{'name':'Mock Away','ordering':'away'}],
            'title':'模拟比赛','slug':'mock-event',
            'markets':[{'id':'20','question':'模拟胜负','groupItemTitle':'主胜','sportsMarketType':'moneyline',
                        'acceptingOrders':True,'closed':False,'outcomes':'["Yes","No"]','outcomePrices':'["0.42","0.58"]'}]}


def request(client,source_id='100',sport='football'):
    return fetch_live_markets(client,source_id=source_id,sport=sport,starts_at=NOW,home='Mock Home',away='Mock Away')


def test_mock_identity_prices_and_unknown():
    raw=event(); raw['markets'][0]['outcomePrices']='["0.42","not-a-price"]'
    def handler(req):
        if '/events/slug/' in req.url.path:
            return httpx.Response(404)
        assert req.url.params['game_id']=='100'
        return httpx.Response(200,json={'events':[raw]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        data=request(client)
    assert data['match_status']=='source_identity' and data['observed_at']
    assert data['events'][0]['markets'][0]['outcomes']==[{'label':'Yes','price':'0.42'},{'label':'No','price':None}]


@pytest.mark.parametrize('game_id, delta, expected', [(None, 0, True), (101, 0, False), (None, 61, False)])
def test_extra_football_totals_require_verified_parent_and_matching_fixture(game_id, delta, expected):
    from datetime import timedelta

    main = event()
    extra = event('2', game_id)
    extra.update(slug='mock-event-more-markets', closed=False, startTime=(NOW + timedelta(seconds=delta)).isoformat())
    extra['markets'] += [
        {'id':'21','sportsMarketType':'totals','question':'O/U 2.5','outcomes':['Over','Under']},
        {'id':'22','sportsMarketType':'first_half_totals','question':'First half O/U 2.5'},
    ]

    def handler(req):
        if req.url.path == '/events/slug/mock-event-more-markets':
            return httpx.Response(200, json=extra)
        assert req.url.path == '/events/keyset'
        return httpx.Response(200, json={'events':[main]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        events = request(client)['events']
    assert [row['id'] for row in events] == (['1','2'] if expected else ['1'])
    if expected:
        assert events[1]['read_only'] is True
        assert [market['id'] for market in events[1]['markets']] == ['21']


def test_mock_mismatched_identity_does_not_show_market():
    raw=event(game=101)
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'events':[raw]}))) as client:
        assert request(client)['events']==[]


def test_mock_nba_multiple_distinct_games_not_guessed():
    first,second=event('1',100,'nba'),event('2',101,'nba')
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'events':[first,second]}))) as client:
        with pytest.raises(SourceError,match='ambiguous_game'): request(client,None,'nba')


def test_mock_nba_one_game_is_candidate_only():
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'events':[event(sport='nba')]}))) as client:
        result=request(client,None,'nba')
    assert result['match_status']=='candidate' and len(result['events'])==1


def test_mock_later_page_error_returns_no_partial_data():
    def handler(req):
        if req.url.params.get('after_cursor'): return httpx.Response(503)
        return httpx.Response(200,json={'events':[event()],'next_cursor':'next'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError,match='http_503'): request(client)


def test_mock_full_page_without_cursor_is_error():
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'events':[event()]}))) as client:
        with pytest.raises(SourceError,match='invalid_pagination'):
            fetch_live_markets(client,source_id='100',sport='football',starts_at=NOW,home='Mock Home',away='Mock Away',page_size=1)


@pytest.mark.parametrize('kind',['moneyline','spreads','totals'])
def test_full_match_types_are_kept(kind):
    assert supported_full_game_market({'sportsMarketType':kind,'question':'Mock Home vs Mock Away','slug':'mock-match'})


@pytest.mark.parametrize('kind',['soccer_halftime_result','first_half_totals','second_half_totals',
    'soccer_team_totals','soccer_first_half_team_totals','points','assists','rebounds','threes',None,'unknown'])
def test_other_types_are_excluded(kind):
    assert not supported_full_game_market({'sportsMarketType':kind,'question':'Mock market'})


@pytest.mark.parametrize('label',['First Half O/U 1.5','Second Quarter Spread','Mock Team Total','1H total'])
def test_partial_game_text_is_excluded_even_with_supported_type(label):
    assert not supported_full_game_market({'sportsMarketType':'totals','question':label})


def test_full_match_filter_preserves_source_events_for_three_categories():
    first=event();first['markets']=[
        {'id':'20','sportsMarketType':'moneyline','question':'Mock game','outcomes':['Yes','No']},
        {'id':'21','sportsMarketType':'points','question':'Mock player points','outcomes':['Yes','No']},
        {'id':'22','sportsMarketType':'totals','question':'Mock game total','outcomes':['Over','Under']},
        {'id':'23','sportsMarketType':'first_half_totals','question':'First half total','outcomes':['Over','Under']},
    ]
    second=event(ident='2');second['markets']=[{'id':'24','sportsMarketType':'spreads','question':'Spread: Mock Home (-1.5)','outcomes':['Yes','No']}]
    third=event(ident='3');third['markets']=[{'id':'25','sportsMarketType':'assists','question':'Mock player assists','outcomes':['Yes','No']}]
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'events':[first,second,third]}))) as client:
        data=request(client)
    assert [(row['id'],[m['type'] for m in row['markets']]) for row in data['events']]==[('1',['moneyline','totals']),('2',['spreads'])]
