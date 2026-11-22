"""外部赛果候选测试；全部赛事均为模拟数据。"""
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from types import SimpleNamespace as S

import pytest
from backend.result_binding import (match_fixture_candidates, match_fixture_candidates_detailed,
                                    unique_nba_fixture_id)
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


def test_football_directory_alias_matches_api_name_without_breaking_strict_rules():
    api=[fixture('1545662','FYR Macedonia','Slovenia')]
    assert [x.external_id for x in match_fixture_candidates(
        'football',START,'North Macedonia','Slovenia',api)]==['1545662']
    reverse=[fixture('1545662','North Macedonia','Slovenia')]
    assert [x.external_id for x in match_fixture_candidates(
        'football',START,'FYR Macedonia','Slovenia',reverse)]==['1545662']
    swapped=[fixture('1545662','Slovenia','FYR Macedonia')]
    assert match_fixture_candidates('football',START,'North Macedonia','Slovenia',swapped)==[]
    delayed=[fixture('1545662','FYR Macedonia','Slovenia',offset=121)]
    assert match_fixture_candidates('football',START,'North Macedonia','Slovenia',delayed)==[]


def test_verified_database_aliases_expand_football_matching():
    rows=[fixture('1','Mock Alias','Mock Away')]
    assert match_fixture_candidates('football',START,'Mock Home','Mock Away',rows)==[]
    assert [x.external_id for x in match_fixture_candidates(
        'football',START,'Mock Home','Mock Away',rows,home_aliases={'Mock Alias'})]==['1']


def test_nba_explicit_alias_and_ambiguity_remain_visible():
    rows=[fixture('1','New Orleans Pelicans','Denver Nuggets',sport='nba'),
          fixture('2','New Orleans Pelicans','Denver Nuggets',sport='nba',offset=30)]
    assert [x.external_id for x in match_fixture_candidates('nba',START,'Pelicans','Nuggets',rows)]==['1','2']


def test_diagnostics_distinguish_reversed_order_from_missing_fixture():
    reversed_row=fixture('42','Utah Jazz','LA Clippers',sport='nba')
    matches,info=match_fixture_candidates_detailed('nba',START,'Clippers','Jazz',[reversed_row])
    assert matches==[] and info['reversed_order']==1
    assert info['time_window_rows']==1 and info['home_matches']==0

    unrelated=fixture('43','Boston Celtics','Utah Jazz',sport='nba')
    matches,info=match_fixture_candidates_detailed('nba',START,'Clippers','Jazz',[unrelated])
    assert matches==[] and info['reversed_order']==0 and info['home_matches']==0

    # 时间窗口外不应计入候选，但仍能在诊断里看出行数差异。
    late=fixture('44','LA Clippers','Utah Jazz',sport='nba',offset=121)
    matches,info=match_fixture_candidates_detailed('nba',START,'Clippers','Jazz',[late])
    assert matches==[] and info['provider_rows']==1 and info['time_window_rows']==0


def test_nba_directory_aliases_bridge_polymarket_and_balldontlie_names():
    # Polymarket 写 Clippers，balldontlie 写 LA Clippers；两侧必须能对上。
    rows=[fixture('42','LA Clippers','Utah Jazz',sport='nba')]
    assert [x.external_id for x in match_fixture_candidates('nba',START,'Clippers','Jazz',rows)]==['42']
    reverse=[fixture('42','Los Angeles Clippers','Utah Jazz',sport='nba')]
    assert [x.external_id for x in match_fixture_candidates('nba',START,'Clippers','Jazz',reverse)]==['42']
    # 别名扩展仍要求主客顺序一致，且不能跨队误配。
    swapped=[fixture('42','Utah Jazz','LA Clippers',sport='nba')]
    assert match_fixture_candidates('nba',START,'Clippers','Jazz',swapped)==[]
    unrelated=[fixture('42','LA Lakers','Utah Jazz',sport='nba')]
    assert match_fixture_candidates('nba',START,'Clippers','Jazz',unrelated)==[]


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
    monkeypatch.setattr(module,'find_fixture_candidates',lambda *args,**kwargs:rows)
    settings=S(api_football_key=None,balldontlie_key=None)
    assert module.process_pending_binding(None,settings,None)
    assert request.state==expected
    assert request.fixture_id==('42' if expected=='bound' else None)
    if occupied:assert request.last_error_code=='fixture_already_bound'


def test_worker_football_passes_verified_aliases(monkeypatch):
    from backend import result_binding as module
    request=S(id='mock-binding',game_id='mock-game',provider='api_football',state='pending',
              fixture_id=None,candidates=[],last_error_code='no_matching_fixture',
              next_attempt_at=START,updated_at=START)
    game=S(id='mock-game',sport='football',starts_at=START,home_team_id='home',away_team_id='away')
    teams={'home':S(id='home',source_name='North Macedonia',name_zh=None,translation_status='missing'),
           'away':S(id='away',source_name='Slovenia',name_zh=None,translation_status='missing')}
    captured={}
    class Db:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def begin(self):return nullcontext()
        def scalar(self,query):return request
        def scalars(self,query):return iter([])
        def get(self,model,ident):return game if ident=='mock-game' else teams[ident]
        def execute(self,*args,**kwargs):return None
        def flush(self):pass
    def find(*args,**kwargs):
        captured.update(kwargs)
        return [fixture('1545662','FYR Macedonia','Slovenia')]
    monkeypatch.setattr(module,'Session',Db)
    monkeypatch.setattr(module,'db_now',lambda db:START)
    monkeypatch.setattr(module,'find_fixture_candidates',find)
    settings=S(api_football_key=S(get_secret_value=lambda:'mock-key'),balldontlie_key=None)
    assert module.process_pending_binding(None,settings,None)
    assert request.state=='needs_review' and request.last_error_code is None
    assert captured['home_aliases']=={'North Macedonia'}
    assert captured['away_aliases']=={'Slovenia'}


@pytest.mark.parametrize(('home', 'away', 'api_home', 'api_away'), [
    ('Real Racing Club', 'Real Sociedad de Fútbol', 'Racing Santander', 'Real Sociedad'),
    ('Olympique de Marseille', 'Le Mans FC', 'Marseille', 'Le Mans'),
    ('CA Rosario Central', 'CA Gimnasia y Esgrima de Mendoza', 'Rosario Central', 'Gimnasia M.'),
])
def test_football_catalog_matches_reported_team_aliases(home, away, api_home, api_away):
    rows = [fixture('1', api_home, api_away)]
    assert match_fixture_candidates('football', START, home, away, rows) == rows


def test_mendoza_alias_never_matches_gimnasia_la_plata():
    rows = [fixture('1', 'Rosario Central', 'Gimnasia L.P.')]
    assert match_fixture_candidates(
        'football', START, 'CA Rosario Central', 'CA Gimnasia y Esgrima de Mendoza', rows
    ) == []
