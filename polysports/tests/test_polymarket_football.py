"""模拟 Gamma 响应；不执行交易。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.polymarket_football import discover_football
from backend.sports_sources import SourceError


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def mock_event(event_id='1', game_id=10, market_type='moneyline', when=START + timedelta(hours=1)):
    return {'id': event_id, 'gameId': game_id, 'active': True, 'closed': False,
            'startTime': when.isoformat(), 'tags': [{'slug': 'soccer'}],
            'teams': [{'id': 11, 'name': 'Mock Home', 'ordering': 'home'},
                      {'id': 12, 'name': 'Mock Away', 'ordering': 'away'}],
            'markets': [{'id': '100' + event_id, 'sportsMarketType': market_type,
                         'active': True, 'closed': False, 'gameStartTime': when.isoformat()}]}


def test_mock_discovery_filters_and_merges_pages():
    first = mock_event()
    second = mock_event('2')
    halftime = mock_event('3', 11, 'soccer_halftime_result')
    before = mock_event('4', 12, when=START - timedelta(seconds=1))
    end = mock_event('5', 13, when=START + timedelta(hours=36))
    other = mock_event('6', 14); other['tags'] = [{'slug': 'nba'}]
    def handler(request):
        assert request.url.params['tag_slug'] == 'soccer'
        if not request.url.params.get('after_cursor'):
            return httpx.Response(200, json={'events': [first, halftime, before], 'next_cursor': 'next'})
        return httpx.Response(200, json={'events': [second, end, other], 'next_cursor': None})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        games, pages, received = discover_football(client, START, page_size=4)
    assert (pages, received, len(games)) == (2, 6, 1)
    assert games[0].game_id == '10' and games[0].event_ids == ('1', '2')
    assert games[0].market_ids == ('1001', '1002') and games[0].home.name == 'Mock Home'


@pytest.mark.parametrize('change', ['missing_teams', 'wrong_market_time', 'closed_market', 'missing_start', 'wrong_tag'])
def test_mock_incomplete_or_nonmatching_event_is_not_a_game(change):
    event = mock_event()
    if change == 'missing_teams': event['teams'] = []
    elif change == 'wrong_market_time': event['markets'][0]['gameStartTime'] = (START + timedelta(hours=2)).isoformat()
    elif change == 'closed_market': event['markets'][0]['closed'] = True
    elif change == 'missing_start': event['startTime'] = None
    else: event['tags'] = []
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'events': [event], 'next_cursor': None}))) as client:
        assert discover_football(client, START)[0] == []


def test_mock_conflicting_game_identity_fails_closed():
    one, two = mock_event(), mock_event('2')
    two['teams'][0]['id'] = 99
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'events': [one, two], 'next_cursor': None}))) as client:
        with pytest.raises(SourceError, match='conflicting_game'): discover_football(client, START)


@pytest.mark.parametrize('body,code', [
    ({'events': [], 'next_cursor': 'next'}, 'pagination_loop'),
    ({'unexpected': []}, 'invalid_pagination'),
    ({'events': [mock_event(), deepcopy(mock_event())], 'next_cursor': None}, 'pagination_duplicate'),
])
def test_mock_bad_pagination_never_returns_partial_results(body, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as client:
        with pytest.raises(SourceError, match=code): discover_football(client, START)


def test_mock_later_page_failure_never_returns_partial_results():
    def handler(request):
        if request.url.params.get('after_cursor'): return httpx.Response(503)
        return httpx.Response(200, json={'events': [mock_event()], 'next_cursor': 'next'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceError, match='http_503'): discover_football(client, START)


def test_mock_last_page_may_omit_cursor():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'events': [mock_event()]}))) as client:
        assert len(discover_football(client, START)[0]) == 1
