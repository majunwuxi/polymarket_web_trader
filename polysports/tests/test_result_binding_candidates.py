"""外部赛果候选测试；全部赛事均为模拟数据。"""
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from types import SimpleNamespace as S

import pytest
from backend.result_binding import match_fixture_candidates, unique_nba_fixture_id
from backend.sports_sources import SourceGame, SourceTeam, SourceError

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


def test_nba_autobind_requires_one_valid_numeric_fixture():
    row=fixture('42','New Orleans Pelicans','Denver Nuggets',sport='nba')
    matches=match_fixture_candidates('nba',START,'Pelicans','Nuggets',[row])
    assert unique_nba_fixture_id('nba',matches)=='42'
    assert unique_nba_fixture_id('football',matches) is None
    assert unique_nba_fixture_id('nba',matches*2) is None
    assert unique_nba_fixture_id('nba',[fixture('bad','New Orleans Pelicans','Denver Nuggets',sport='nba')]) is None


def test_duplicate_id_with_conflicting_schedule_is_rejected():
    rows=[fixture('42','New Orleans Pelicans','Denver Nuggets',sport='nba'),
          fixture('42','New Orleans Pelicans','Denver Nuggets',sport='nba',offset=30)]
    with pytest.raises(SourceError) as error:
        match_fixture_candidates('nba',START,'Pelicans','Nuggets',rows)
    assert error.value.code=='conflicting_fixture'


@pytest.mark.parametrize(('initial','matches','occupied','expected'),[
    ('pending',['42'],False,'bound'),
    ('needs_review',['42'],False,'bound'),
    ('pending',['42','43'],False,'needs_review'),
    ('pending',['42'],True,'needs_review'),
])
def test_worker_autobind_and_conflict_with_mocked_db(monkeypatch,initial,matches,occupied,expected):
    from backend import result_binding as module
    request=S(id='mock-binding',game_id='mock-game',provider='balldontlie',state=initial,
              fixture_id=None,candidates=[],last_error_code=None,next_attempt_at=START,updated_at=START)
    game=S(id='mock-game',sport='nba',starts_at=START,home_team_id='home',away_team_id='away')
    teams={'home':S(source_name='Pelicans'),'away':S(source_name='Nuggets')}
    class Db:
        def __init__(self,*args):self.calls=0
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def begin(self):return nullcontext()
        def begin_nested(self):return nullcontext()
        def scalar(self,query):
            self.calls+=1
            return request if self.calls==1 else ('other-binding' if occupied else None)
        def get(self,model,ident):return game if ident=='mock-game' else teams[ident]
        def execute(self,*args,**kwargs):return None
        def flush(self):pass
    monkeypatch.setattr(module,'Session',Db)
    monkeypatch.setattr(module,'db_now',lambda db:START)
    rows=[fixture(ident,'New Orleans Pelicans','Denver Nuggets',sport='nba') for ident in matches]
    monkeypatch.setattr(module,'find_fixture_candidates',lambda *args:rows)
    settings=S(api_football_key=None,balldontlie_key=None)
    assert module.process_pending_binding(None,settings,None)
    assert request.state==expected
    assert request.fixture_id==('42' if expected=='bound' else None)
    if occupied:assert request.last_error_code=='fixture_already_bound'
