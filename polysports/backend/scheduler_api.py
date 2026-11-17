from __future__ import annotations
import uuid
from datetime import datetime,time,timedelta,timezone
from typing import Literal
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, text, tuple_
from sqlalchemy.orm import Session
from . import auth
from .models import AuditEvent, ComboLeg, ComboRFQ, Game, Job, JobRun, MarketOutcome, ScheduleRevision, SyncRun, SyncSchedule, Team, TradeIntent, WatchedGame, WorkerHeartbeat
from .scheduler import db_now, watch_game
from .prefetch import objects_by_id, rows_by_attr
from .sports_api import cursor_decode,cursor_encode

router=APIRouter(prefix='/api/v1')


def ok(request,data): return {'data':data,'request_id':request.state.request_id}
def watch_data(watch,job):
    return {'id':str(watch.id),'game_id':str(watch.game_id),'state':watch.state,'first_due_at':watch.first_due_at.isoformat(),'last_checked_at':watch.last_checked_at.isoformat() if watch.last_checked_at else None,'completed_at':watch.completed_at.isoformat() if watch.completed_at else None,'next_due_at':job.due_at.isoformat() if job and job.status in ('pending','leased') else None,'job_status':job.status if job else None}


def team_name(team):
    return (team.name_zh or team.source_name) if team else '未知球队'


def game_target_data(game,home,away):
    return {'type':'game','game_id':str(game.id),'label':team_name(home)+' 对 '+team_name(away),
            'starts_at':game.starts_at.isoformat(),'game_status':game.status,'sport':game.sport}


def game_target(db, game_id):
    game=db.get(Game,game_id)
    if not game: return None
    return game_target_data(game,db.get(Team,game.home_team_id),db.get(Team,game.away_team_id))


def job_targets(db, jobs):
    jobs=list(jobs)
    if not jobs: return {}
    watches=objects_by_id(db,WatchedGame,{job.watched_game_id for job in jobs if job.kind=='result_poll'})
    intents=objects_by_id(db,TradeIntent,{job.trade_intent_id for job in jobs if job.kind=='trade_reconcile'})
    combos=objects_by_id(db,ComboRFQ,{job.combo_rfq_id for job in jobs if job.kind=='combo_reconcile'})
    runs=objects_by_id(db,SyncRun,{job.sync_run_id for job in jobs if job.kind=='sync'})
    combo_legs=rows_by_attr(db,ComboLeg,'rfq_id',list(combos))
    game_ids={watch.game_id for watch in watches.values()} | {intent.game_id for intent in intents.values()} | {leg.game_id for values in combo_legs.values() for leg in values}
    games=objects_by_id(db,Game,game_ids)
    teams=objects_by_id(db,Team,
                        {game.home_team_id for game in games.values()} |
                        {game.away_team_id for game in games.values()})
    outcomes=objects_by_id(db,MarketOutcome,{intent.outcome_id for intent in intents.values()})

    def for_game(game_id):
        game=games.get(game_id)
        return game_target_data(game,teams.get(game.home_team_id),teams.get(game.away_team_id)) if game else None

    result={}
    for job in jobs:
        target=None
        if job.kind=='result_poll':
            watch=watches.get(job.watched_game_id)
            target=for_game(watch.game_id) if watch else None
        elif job.kind=='trade_reconcile':
            intent=intents.get(job.trade_intent_id)
            if intent:
                target=for_game(intent.game_id) or {'type':'game','label':'比赛不可用'}
                outcome=outcomes.get(intent.outcome_id)
                target.update({'type':'trade','intent_id':str(intent.id),'trade_state':intent.state,
                               'side':intent.side,'label':target['label']+(' · '+outcome.label if outcome else '')})
        elif job.kind=='combo_reconcile':
            combo=combos.get(job.combo_rfq_id)
            if combo:
                targets=[x for x in (for_game(leg.game_id) for leg in combo_legs.get(combo.id,[])) if x]
                target={'type':'combo','combo_id':str(combo.id),'state':combo.state,
                        'label':'；'.join(x['label'] for x in targets) if targets else '组合腿不可用',
                        'starts_at':min((x['starts_at'] for x in targets),default=None),'legs':targets}
        elif job.kind=='sync':
            run=runs.get(job.sync_run_id)
            if run: target={'type':'sync','run_id':str(run.id),'state':run.status,'label':run.sport+' · '+run.provider}
        result[job.id]=target
    return result


def job_target(db, job):
    if job.kind=='result_poll':
        watch=db.get(WatchedGame,job.watched_game_id)
        return game_target(db,watch.game_id) if watch else None
    if job.kind=='trade_reconcile':
        intent=db.get(TradeIntent,job.trade_intent_id)
        if not intent: return None
        target=game_target(db,intent.game_id) or {'type':'game','label':'比赛不可用'}
        outcome=db.get(MarketOutcome,intent.outcome_id)
        target.update({'type':'trade','intent_id':str(intent.id),'trade_state':intent.state,'side':intent.side,
                       'label':target['label']+(' · '+outcome.label if outcome else '')})
        return target
    if job.kind=='combo_reconcile':
        combo=db.get(ComboRFQ,job.combo_rfq_id)
        if not combo: return None
        legs=list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==combo.id)))
        targets=[x for x in (game_target(db,leg.game_id) for leg in legs) if x]
        return {'type':'combo','combo_id':str(combo.id),'state':combo.state,
                'label':'；'.join(x['label'] for x in targets) if targets else '组合腿不可用',
                'starts_at':min((x['starts_at'] for x in targets),default=None),'legs':targets}
    if job.kind=='sync':
        run=db.get(SyncRun,job.sync_run_id)
        return {'type':'sync','run_id':str(run.id),'state':run.status,'label':run.sport+' · '+run.provider} if run else None
    return None


@router.post('/games/{game_id}/watch')
def add_watch(request: Request,game_id:uuid.UUID):
    with Session(request.app.state.engine) as db, db.begin():
        game=db.get(Game,game_id)
        if game and game.provider=='polymarket': return auth.error(request,409,'BINDING_REQUIRED','赛事赛果来源尚未绑定，当前不能关注或下单。')
        watch=watch_game(db,game_id)
        if watch is None: return auth.error(request,404,'NOT_FOUND','赛事不存在')
        db.flush(); job=db.scalar(select(Job).where(Job.watched_game_id==watch.id))
        return ok(request,watch_data(watch,job))


@router.get('/watches')
def watches(request: Request,sport:Literal['football','nba']|None=None,state:Literal['active','completed','paused']|None=None,limit:int=Query(50,ge=1,le=100),cursor:str|None=None):
    end_of_time=datetime(9999,1,1,tzinfo=timezone.utc)
    due=select(func.min(Job.due_at)).where(Job.watched_game_id==WatchedGame.id,Job.kind=='result_poll',Job.status.in_(['pending','leased'])).correlate(WatchedGame).scalar_subquery()
    sort_due=func.coalesce(due,end_of_time)
    query=select(WatchedGame,sort_due.label('sort_due')).join(Game,Game.id==WatchedGame.game_id)
    if sport:query=query.where(Game.sport==sport)
    if state:query=query.where(WatchedGame.state==state)
    try:
        if cursor:
            stamp,ident=cursor_decode(cursor);stamp=datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:raise ValueError()
            query=query.where(tuple_(sort_due,WatchedGame.id)>tuple_(stamp,uuid.UUID(ident)))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.execute(query.order_by(sort_due,WatchedGame.id).limit(limit+1)));items=rows[:limit]
        return ok(request,{'items':[watch_data(row[0],db.scalar(select(Job).where(Job.watched_game_id==row[0].id,Job.kind=='result_poll'))) for row in items],'next_cursor':cursor_encode([items[-1].sort_due.isoformat(),str(items[-1][0].id)]) if len(rows)>limit else None})


@router.get('/sync-schedules')
def schedules(request: Request):
    with Session(request.app.state.engine) as db:
        revision=db.get(ScheduleRevision,1)
        items=db.scalars(select(SyncSchedule).where(SyncSchedule.enabled.is_(True)).order_by(SyncSchedule.sport,SyncSchedule.utc_time))
        return ok(request,{'revision':revision.revision,'items':[{'id':str(x.id),'sport':x.sport,'provider':x.provider,'utc_time':x.utc_time.isoformat(),'enabled':x.enabled} for x in items]})


class ScheduleItem(BaseModel):
    sport:Literal['football','nba']
    provider:Literal['polymarket','balldontlie']
    utc_time:time
    enabled:bool=True
    @model_validator(mode='after')
    def valid(self):
        if self.utc_time.tzinfo is not None or self.provider!='polymarket': raise ValueError('invalid schedule')
        return self


class ScheduleUpdate(BaseModel):
    revision:int=Field(ge=1)
    items:list[ScheduleItem]=Field(min_length=2,max_length=48)
    @model_validator(mode='after')
    def validate_items(self):
        values=[(x.provider,x.sport,x.utc_time) for x in self.items]
        if len(values)!=len(set(values)) or {x.sport for x in self.items if x.enabled}!={'football','nba'}: raise ValueError('invalid schedule list')
        return self


@router.put('/sync-schedules')
def update_schedules(request: Request,body:ScheduleUpdate):
    with Session(request.app.state.engine) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('polysports-scheduler'))"))
        rev=db.scalar(select(ScheduleRevision).where(ScheduleRevision.id==1).with_for_update())
        if rev.revision!=body.revision: return auth.error(request,409,'CONFLICT','计划已被修改，请刷新后重试')
        now=db_now(db); existing=list(db.scalars(select(SyncSchedule).with_for_update()))
        wanted={(x.provider,x.sport,x.utc_time):x.enabled for x in body.items}
        for item in existing:
            item.enabled=wanted.pop((item.provider,item.sport,item.utc_time),False); item.updated_at=now
            if not item.enabled:
                jobs=list(db.scalars(select(Job).where(Job.schedule_id==item.id,Job.status=='pending',Job.due_at>now).with_for_update()))
                for job in jobs:
                    job.status='cancelled'; job.updated_at=now
                    run=db.get(SyncRun,job.sync_run_id); run.status='failed'; run.error_code='schedule_changed'; run.error_summary='计划已修改'; run.finished_at=now
        for (provider,sport,utc_time),enabled in wanted.items():
            db.add(SyncSchedule(id=uuid.uuid4(),provider=provider,sport=sport,utc_time=utc_time,enabled=enabled))
        rev.revision+=1
        db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='sync_schedules_updated',details={'revision':rev.revision}))
        db.flush()
        items=db.scalars(select(SyncSchedule).where(SyncSchedule.enabled.is_(True)).order_by(SyncSchedule.sport,SyncSchedule.utc_time))
        return ok(request,{'revision':rev.revision,'items':[{'id':str(x.id),'sport':x.sport,'provider':x.provider,'utc_time':x.utc_time.isoformat(),'enabled':x.enabled} for x in items]})


class ManualSync(BaseModel):
    sport:Literal['football','nba']


@router.post('/sync-runs',status_code=202)
def queue_sync(request: Request,body:ManualSync):
    provider='polymarket'
    with Session(request.app.state.engine) as db, db.begin():
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:name))'),{'name':'manual-sync:'+provider+':'+body.sport})
        existing=db.scalar(select(Job).join(SyncRun,Job.sync_run_id==SyncRun.id).where(SyncRun.provider==provider,SyncRun.sport==body.sport,SyncRun.trigger_type=='manual',Job.status.in_(['pending','leased'])))
        if existing: return ok(request,{'run_id':str(existing.sync_run_id),'job_id':str(existing.id),'status':existing.status})
        now=db_now(db); run=SyncRun(id=uuid.uuid4(),provider=provider,sport=body.sport,trigger_type='manual',scheduled_for=now,status='queued'); db.add(run); db.flush()
        job=Job(id=uuid.uuid4(),kind='sync',dedupe_key='manual:'+str(run.id),sync_run_id=run.id,due_at=now,status='pending'); db.add(job)
        return ok(request,{'run_id':str(run.id),'job_id':str(job.id),'status':'queued'})


@router.get('/jobs')
def jobs(request: Request,kind:Literal['sync','result_poll','trade_reconcile','combo_reconcile','cleanup'] | None=None,status:Literal['pending','leased','done','cancelled','failed'] | None=None,game_id:uuid.UUID|None=None,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),limit:int=Query(100,ge=1,le=100),cursor:str|None=None):
    now=datetime.now(timezone.utc)
    start=start or now-timedelta(hours=20);end=end or now+timedelta(hours=24)
    if start.tzinfo is None or end.tzinfo is None or end<=start:return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    query=select(Job).where(Job.due_at>=start,Job.due_at<end)
    if kind: query=query.where(Job.kind==kind)
    if status: query=query.where(Job.status==status)
    if game_id:query=query.join(WatchedGame,WatchedGame.id==Job.watched_game_id).where(WatchedGame.game_id==game_id)
    try:
        if cursor:
            stamp,ident=cursor_decode(cursor);stamp=datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:raise ValueError()
            query=query.where(tuple_(Job.due_at,Job.id)>tuple_(stamp,uuid.UUID(ident)))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(Job.due_at,Job.id).limit(limit+1)));items=rows[:limit]
        targets=job_targets(db,items)
        return ok(request,{'items':[{'id':str(x.id),'kind':x.kind,'status':x.status,'due_at':x.due_at.isoformat(),'attempt_count':x.attempt_count,'failure_count':x.failure_count,'last_error_code':x.last_error_code,'last_error_summary':x.last_error_summary,'watched_game_id':str(x.watched_game_id) if x.watched_game_id else None,'target':targets.get(x.id)} for x in items],'window':{'from':start.isoformat(),'to':end.isoformat()},'next_cursor':cursor_encode([items[-1].due_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None})


@router.get('/jobs/{job_id}/runs')
def job_runs(request:Request,job_id:uuid.UUID,start:datetime|None=Query(None,alias='from'),end:datetime|None=Query(None,alias='to'),limit:int=Query(30,ge=1,le=100),cursor:str|None=None):
    now=datetime.now(timezone.utc)
    start=start or now-timedelta(hours=20);end=end or now+timedelta(hours=24)
    if start.tzinfo is None or end.tzinfo is None or end<=start:return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    query=select(JobRun).where(JobRun.job_id==job_id,JobRun.started_at>=start,JobRun.started_at<end)
    try:
        if cursor:
            attempt,ident=cursor_decode(cursor);attempt=int(attempt);ident=uuid.UUID(ident)
            if attempt<1:raise ValueError()
            query=query.where(tuple_(JobRun.attempt_no,JobRun.id)<tuple_(attempt,ident))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        if not db.get(Job,job_id): return auth.error(request,404,'NOT_FOUND','任务不存在')
        rows=list(db.scalars(query.order_by(JobRun.attempt_no.desc(),JobRun.id.desc()).limit(limit+1)));items=rows[:limit]
        return ok(request,{'items':[{'attempt_no':x.attempt_no,'started_at':x.started_at.isoformat(),'finished_at':x.finished_at.isoformat() if x.finished_at else None,'result':x.result,'error_code':x.error_code} for x in items],'window':{'from':start.isoformat(),'to':end.isoformat()},'next_cursor':cursor_encode([str(items[-1].attempt_no),str(items[-1].id)]) if len(rows)>limit else None})


@router.get('/worker-status')
def worker_status(request:Request):
    with Session(request.app.state.engine) as db:
        now=db_now(db); rows=db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(20))
        return ok(request,{'workers':[{'id':str(x.worker_id),'state':x.state if (now-x.last_seen_at).total_seconds()<60 else 'unavailable','last_seen_at':x.last_seen_at.isoformat()} for x in rows]})
