from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Game, GameRevision, League, SyncRun, Team, TeamSourceId
from .sports_sources import SourceError, SourceGame, SourceTeam, fetch_schedule


def upsert_team(db: Session, provider: str, sport: str, source: SourceTeam) -> Team:
    mapping = db.scalar(select(TeamSourceId).where(TeamSourceId.provider == provider, TeamSourceId.external_id == source.external_id))
    if mapping is None:
        obj = Team(id=uuid.uuid4(), sport=sport, source_name=source.name, logo_url=source.logo_url, translation_status='missing')
        db.add(obj); db.flush()
        db.add(TeamSourceId(id=uuid.uuid4(),team_id=obj.id,provider=provider,external_id=source.external_id))
    else:
        obj = db.get(Team, mapping.team_id)
        if obj is None or obj.sport != sport:
            raise ValueError('team sport mismatch')
        obj.source_name = source.name
        if source.logo_url:
            obj.logo_url = source.logo_url
        obj.updated_at = datetime.now(timezone.utc)
    db.flush()
    return obj


def upsert_game(db: Session, source: SourceGame, run_id: uuid.UUID) -> None:
    home = upsert_team(db, source.provider, source.sport, source.home)
    away = upsert_team(db, source.provider, source.sport, source.away)
    league = None
    if source.league_id:
        league = db.scalar(select(League).where(League.provider == source.provider, League.external_id == source.league_id))
        if league is None:
            league = League(id=uuid.uuid4(),provider=source.provider,external_id=source.league_id,sport=source.sport,name=source.league_name,logo_url=source.league_logo)
            db.add(league); db.flush()
        else:
            if league.sport != source.sport:
                raise ValueError('league sport mismatch')
            league.name, league.logo_url = source.league_name, source.league_logo
            league.updated_at = datetime.now(timezone.utc)
    game = db.scalar(select(Game).where(Game.provider == source.provider, Game.external_id == source.external_id).with_for_update())
    now = datetime.now(timezone.utc)
    if game is None:
        game = Game(id=uuid.uuid4(),provider=source.provider,external_id=source.external_id,sport=source.sport,home_team_id=home.id,away_team_id=away.id,starts_at=source.starts_at,status=source.status)
        db.add(game)
    else:
        if game.sport != source.sport:
            raise ValueError('game sport mismatch')
        if (game.starts_at,game.status,game.home_score,game.away_score) != (source.starts_at,source.status,source.home_score,source.away_score):
            db.add(GameRevision(id=uuid.uuid4(),game_id=game.id,old_starts_at=game.starts_at,new_starts_at=source.starts_at,old_status=game.status,new_status=source.status,provider_status=source.provider_status,home_score=source.home_score,away_score=source.away_score,source_run_id=run_id,change_reason='source_update'))
    game.home_team_id, game.away_team_id = home.id, away.id
    game.league_id = league.id if league else None
    game.starts_at, game.status, game.provider_status = source.starts_at, source.status, source.provider_status
    game.home_score, game.away_score = source.home_score, source.away_score
    game.last_synced_at = game.updated_at = now
    db.flush()


def run_sync(engine, provider: str, key: str, *, client: httpx.Client | None = None, now: datetime | None = None) -> uuid.UUID:
    if provider not in ('api_football','balldontlie'):
        raise ValueError('unknown provider')
    start = now or datetime.now(timezone.utc)
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError('now must be timezone-aware')
    start = start.astimezone(timezone.utc)
    run_id = uuid.uuid4()
    with Session(engine) as db, db.begin():
        db.add(SyncRun(id=run_id,provider=provider,sport='football' if provider == 'api_football' else 'nba',trigger_type='manual',scheduled_for=start,started_at=start,status='running',window_start=start,window_end=start+timedelta(hours=36)))
    owned = client is None
    source_client = client or httpx.Client()
    try:
        games, pages, received = fetch_schedule(provider,key,start,source_client)
        if provider == 'balldontlie':
            # 官方默认排除季前赛，单独请求并合并；任一请求失败均不能标记成功。
            preseason, extra_pages, extra_received = fetch_schedule(provider,key,start,source_client,season_type='preseason')
            unique = {game.external_id: game for game in games}
            for game in preseason:
                if game.external_id in unique and unique[game.external_id] != game:
                    raise SourceError('conflicting_fixture')
                unique[game.external_id] = game
            games, pages, received = list(unique.values()), pages + extra_pages, received + extra_received
        # 各来源的写入串行化，唯一键仍由 PostgreSQL 约束；请求阶段不持锁。
        with Session(engine) as db, db.begin():
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:provider))'), {'provider':provider})
            for game in games:
                upsert_game(db,game,run_id)
            run = db.get(SyncRun, run_id)
            run.status, run.pages_fetched, run.items_received, run.items_upserted = 'succeeded', pages, received, len(games)
            run.finished_at = datetime.now(timezone.utc)
    except Exception as exc:
        with Session(engine) as db, db.begin():
            run = db.get(SyncRun,run_id)
            run.status, run.error_code, run.error_summary = 'failed', exc.code if isinstance(exc,SourceError) else 'internal_error', '同步未完成，请检查配置和来源状态'
            run.finished_at = datetime.now(timezone.utc)
        if not isinstance(exc, SourceError):
            raise
    finally:
        if owned:
            source_client.close()
    return run_id
