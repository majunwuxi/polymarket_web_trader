"""只为经过交易所确认的订单持久化待绑定记录。"""
import uuid
from datetime import timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Game, ResultBindingRequest, Team
from .gamma import normal
from .team_directory import NBA_SHORT_NAMES
from .sports_sources import SourceError, fetch_schedule
from .scheduler import db_now, watch_game


def request_binding_for_accepted_order(db, game_id):
    game=db.get(Game,game_id)
    if not game or game.provider!='polymarket': return
    if game.sport not in ('football','nba'): raise ValueError('unsupported sport')
    provider='api_football' if game.sport=='football' else 'balldontlie'
    stmt=insert(ResultBindingRequest).values(id=uuid.uuid4(),game_id=game.id,provider=provider,state='pending')
    db.execute(stmt.on_conflict_do_nothing(index_elements=['game_id']))
    watch_game(db,game.id)


def _team_names(sport, name):
    names={normal(name)}
    if sport=='nba':
        full=next((value for short,value in NBA_SHORT_NAMES.items() if normal(short)==normal(name)),None)
        if full: names.add(normal(full))
    return names


def match_fixture_candidates(sport, starts_at, home_name, away_name, fixtures):
    """匹配结果仅作为候选，不能据此自动认定最终比分或市场结算。"""
    if sport not in ('football','nba'): raise ValueError('unsupported sport')
    home_names,away_names=_team_names(sport,home_name),_team_names(sport,away_name)
    if not all(home_names) or not all(away_names): return []
    matches=[]; seen=set()
    for fixture in fixtures:
        if fixture.sport!=sport or fixture.provider!=('api_football' if sport=='football' else 'balldontlie'): continue
        if abs((fixture.starts_at-starts_at).total_seconds())>7200: continue
        if normal(fixture.home.name) not in home_names or normal(fixture.away.name) not in away_names: continue
        if fixture.external_id in seen: continue
        seen.add(fixture.external_id); matches.append(fixture)
    return sorted(matches,key=lambda item:(item.starts_at,item.external_id))


def find_fixture_candidates(client, key, sport, starts_at, home_name, away_name):
    """在包含比赛时间的 36 小时窗口查询外部数据，不使用推测结果。"""
    provider='api_football' if sport=='football' else 'balldontlie' if sport=='nba' else None
    if not provider: raise ValueError('unsupported sport')
    fixtures,_,_=fetch_schedule(provider,key,starts_at-timedelta(hours=18),client)
    return match_fixture_candidates(sport,starts_at,home_name,away_name,fixtures)


def process_pending_binding(engine, settings, client):
    """领取一个到期请求；行锁确保多个 worker 不会同时处理同一赛事。"""
    with Session(engine) as db, db.begin():
        now=db_now(db)
        request=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.state=='pending',
            ResultBindingRequest.next_attempt_at<=now).order_by(ResultBindingRequest.next_attempt_at)
            .with_for_update(skip_locked=True).limit(1))
        if request is None: return False
        game=db.get(Game,request.game_id)
        home=db.get(Team,game.home_team_id);away=db.get(Team,game.away_team_id)
        secret=settings.api_football_key if request.provider=='api_football' else settings.balldontlie_key
        try:
            matches=find_fixture_candidates(client,secret.get_secret_value() if secret else '',game.sport,
                                           game.starts_at,home.source_name,away.source_name)
            request.candidates=[{'id':item.external_id,'starts_at':item.starts_at.isoformat(),
                'home':item.home.name,'away':item.away.name,'league':item.league_name} for item in matches]
            request.last_error_code=None if matches else 'no_matching_fixture'
            if matches: request.state='needs_review'
        except SourceError as exc:
            request.last_error_code=exc.code
        request.updated_at=now
        request.next_attempt_at=now+timedelta(minutes=10)
        return True
