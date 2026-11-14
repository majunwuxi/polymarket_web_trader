"""外部赛果候选测试；全部赛事均为模拟数据。"""
from datetime import datetime, timedelta, timezone

from backend.result_binding import match_fixture_candidates
from backend.sports_sources import SourceGame, SourceTeam

START=datetime(2026,1,1,tzinfo=timezone.utc)


def fixture(ident,home,away,*,sport='football',offset=0):
    return SourceGame('api_football' if sport=='football' else 'balldontlie',sport,ident,
        START+timedelta(minutes=offset),'scheduled','mock',SourceTeam('1',home,None),
        SourceTeam('2',away,None),None,None,None,None,None)


def test_strict_order_time_and_no_duplicates():
    rows=[fixture('1','Mock Home','Mock Away'),fixture('1','Mock Home','Mock Away'),
          fixture('2','Mock Away','Mock Home'),fixture('3','Mock Home','Mock Away',offset=121),
          fixture('4','Mock Home FC','Mock Away')]
    assert [x.external_id for x in match_fixture_candidates('football',START,'Mock Home','Mock Away',rows)]==['1']


def test_nba_explicit_alias_and_ambiguity_remain_visible():
    rows=[fixture('1','New Orleans Pelicans','Denver Nuggets',sport='nba'),
          fixture('2','New Orleans Pelicans','Denver Nuggets',sport='nba',offset=30)]
    assert [x.external_id for x in match_fixture_candidates('nba',START,'Pelicans','Nuggets',rows)]==['1','2']
