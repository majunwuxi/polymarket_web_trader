from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth
from .models import AuditEvent, Game, GameRevision, League, SyncRun, Team, TeamAlias, WatchedGame, Job

router = APIRouter(prefix='/api/v1')


def envelope(request, data):
    return {'data':data, 'request_id':request.state.request_id}


def cursor_encode(values):
    return base64.urlsafe_b64encode(json.dumps(values,ensure_ascii=False).encode()).decode().rstrip('=')


def cursor_decode(value):
    try:
        data = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value)%4)))
        if not isinstance(data,list) or len(data) != 2: raise ValueError()
        return data
    except Exception as exc:
        raise ValueError('invalid cursor') from exc


def team_data(team):
    return {'id':str(team.id),'sport':team.sport,'source_name':team.source_name,'name_zh':team.name_zh,'translation_status':team.translation_status,'logo_url':team.logo_url}


def game_data(db, game):
    league = db.get(League,game.league_id) if game.league_id else None
    return {'id':str(game.id),'provider':game.provider,'external_id':game.external_id,'sport':game.sport,'starts_at':game.starts_at.isoformat(),'status':game.status,'provider_status':game.provider_status,'home_team':team_data(db.get(Team,game.home_team_id)),'away_team':team_data(db.get(Team,game.away_team_id)),'home_score':game.home_score,'away_score':game.away_score,'league':{'name':league.name_zh or league.name,'logo_url':league.logo_url} if league else None,'last_synced_at':game.last_synced_at.isoformat() if game.last_synced_at else None}


@router.get('/games')
def games(request: Request, sport: Literal['football','nba'], start: datetime | None = Query(None,alias='from'), end: datetime | None = Query(None,alias='to'), status: str | None = None, limit: int = Query(30,ge=1,le=100), cursor: str | None = None):
    now = datetime.now(timezone.utc)
    start, end = start or now, end or (now + timedelta(hours=36))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        return auth.error(request,422,'VALIDATION_ERROR','时间范围必须包含时区且结束时间晚于开始时间')
    query = select(Game).where(Game.sport==sport,Game.starts_at>=start,Game.starts_at<end)
    if status: query = query.where(Game.status==status)
    try:
        if cursor:
            date, ident = cursor_decode(cursor)
            date = datetime.fromisoformat(date)
            if date.tzinfo is None: raise ValueError()
            query = query.where(tuple_(Game.starts_at,Game.id)>tuple_(date,uuid.UUID(ident)))
    except (ValueError,TypeError):
        return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows = list(db.scalars(query.order_by(Game.starts_at,Game.id).limit(limit+1)))
        more = len(rows)>limit
        items = rows[:limit]
        return envelope(request,{'items':[game_data(db,x) for x in items],'next_cursor':cursor_encode([items[-1].starts_at.isoformat(),str(items[-1].id)]) if more else None})


@router.get('/games/{game_id}')
def game_detail(request: Request,game_id: uuid.UUID):
    with Session(request.app.state.engine) as db:
        game = db.get(Game,game_id)
        if not game: return auth.error(request,404,'NOT_FOUND','赛事不存在')
        result = game_data(db,game)
        watch=db.scalar(select(WatchedGame).where(WatchedGame.game_id==game.id))
        job=db.scalar(select(Job).where(Job.watched_game_id==watch.id)) if watch else None
        from .scheduler_api import watch_data
        result['watch']=watch_data(watch,job) if watch else None
        result['recent_revisions'] = [{'observed_at':r.observed_at.isoformat(),'old_starts_at':r.old_starts_at.isoformat() if r.old_starts_at else None,'new_starts_at':r.new_starts_at.isoformat() if r.new_starts_at else None,'old_status':r.old_status,'new_status':r.new_status,'home_score':r.home_score,'away_score':r.away_score} for r in db.scalars(select(GameRevision).where(GameRevision.game_id==game_id).order_by(GameRevision.observed_at.desc()).limit(20))]
        return envelope(request,result)


@router.get('/teams')
def teams(request: Request,sport: Literal['football','nba'] | None = None,translation_status: Literal['verified','missing'] | None = None,q: str | None = Query(None,max_length=100),limit: int = Query(30,ge=1,le=100),cursor: str | None = None):
    query = select(Team)
    if sport: query=query.where(Team.sport==sport)
    if translation_status: query=query.where(Team.translation_status==translation_status)
    if q: query=query.where(Team.source_name.icontains(q,autoescape=True) | Team.name_zh.icontains(q,autoescape=True))
    try:
        if cursor:
            name, ident=cursor_decode(cursor)
            if not isinstance(name,str): raise ValueError()
            query=query.where(tuple_(Team.source_name,Team.id)>tuple_(name,uuid.UUID(ident)))
    except (ValueError,TypeError):
        return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(Team.source_name,Team.id).limit(limit+1))); items=rows[:limit]
        return envelope(request,{'items':[team_data(x) for x in items],'next_cursor':cursor_encode([items[-1].source_name,str(items[-1].id)]) if len(rows)>limit else None})


class TeamUpdate(BaseModel):
    name_zh: str | None = Field(None,max_length=200)
    translation_status: Literal['verified','missing']
    @model_validator(mode='after')
    def validate_name(self):
        if self.translation_status == 'verified' and (not self.name_zh or not self.name_zh.strip()): raise ValueError('missing name')
        if self.translation_status == 'missing' and self.name_zh is not None: raise ValueError('unverified name')
        return self


@router.patch('/teams/{team_id}')
def update_team(request: Request,team_id: uuid.UUID,body: TeamUpdate):
    with Session(request.app.state.engine) as db, db.begin():
        obj=db.get(Team,team_id)
        if not obj: return auth.error(request,404,'NOT_FOUND','球队不存在')
        obj.name_zh=body.name_zh.strip() if body.name_zh else None
        obj.translation_status=body.translation_status
        obj.updated_at=datetime.now(timezone.utc)
        db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='team_translation_updated',entity_type='team',entity_id=team_id,details={}))
        return envelope(request,team_data(obj))


class AliasCreate(BaseModel):
    alias: str = Field(min_length=1,max_length=200)
    language: str = Field(min_length=1,max_length=16)
    verified: bool = False


@router.post('/teams/{team_id}/aliases',status_code=201)
def add_alias(request: Request,team_id: uuid.UUID,body: AliasCreate):
    name=body.alias.strip()
    if not name or not body.language.strip(): return auth.error(request,422,'VALIDATION_ERROR','别名和语言不能为空')
    try:
        with Session(request.app.state.engine) as db, db.begin():
            if not db.get(Team,team_id): return auth.error(request,404,'NOT_FOUND','球队不存在')
            obj=TeamAlias(id=uuid.uuid4(),team_id=team_id,alias=name,normalized_alias=' '.join(name.casefold().split()),language=body.language.strip(),verified=body.verified)
            db.add(obj); db.flush()
            db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='team_alias_added',entity_type='team',entity_id=team_id,details={}))
            return envelope(request,{'id':str(obj.id),'alias':obj.alias,'language':obj.language,'verified':obj.verified})
    except IntegrityError:
        return auth.error(request,409,'CONFLICT','别名已存在')


@router.get('/sync-runs')
def sync_runs(request: Request,sport: Literal['football','nba'] | None = None,limit: int = Query(30,ge=1,le=100)):
    query=select(SyncRun)
    if sport: query=query.where(SyncRun.sport==sport)
    with Session(request.app.state.engine) as db:
        rows=db.scalars(query.order_by(SyncRun.started_at.desc().nullslast(),SyncRun.scheduled_for.desc(),SyncRun.id.desc()).limit(limit))
        return envelope(request,{'items':[{'id':str(x.id),'provider':x.provider,'sport':x.sport,'status':x.status,'started_at':x.started_at.isoformat() if x.started_at else x.scheduled_for.isoformat(),'finished_at':x.finished_at.isoformat() if x.finished_at else None,'pages_fetched':x.pages_fetched,'items_received':x.items_received,'items_upserted':x.items_upserted,'error_code':x.error_code,'error_summary':x.error_summary} for x in rows]})
