from __future__ import annotations
import uuid
from datetime import time
from typing import Literal
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from . import auth
from .models import AuditEvent, Game, Job, JobRun, ScheduleRevision, SyncRun, SyncSchedule, WatchedGame, WorkerHeartbeat
from .scheduler import db_now, watch_game

router=APIRouter(prefix='/api/v1')


def ok(request,data): return {'data':data,'request_id':request.state.request_id}
def watch_data(watch,job):
    return {'id':str(watch.id),'game_id':str(watch.game_id),'state':watch.state,'first_due_at':watch.first_due_at.isoformat(),'last_checked_at':watch.last_checked_at.isoformat() if watch.last_checked_at else None,'completed_at':watch.completed_at.isoformat() if watch.completed_at else None,'next_due_at':job.due_at.isoformat() if job and job.status in ('pending','leased') else None,'job_status':job.status if job else None}


@router.post('/games/{game_id}/watch')
def add_watch(request: Request,game_id:uuid.UUID):
    with Session(request.app.state.engine) as db, db.begin():
        watch=watch_game(db,game_id)
        if watch is None: return auth.error(request,404,'NOT_FOUND','赛事不存在')
        db.flush(); job=db.scalar(select(Job).where(Job.watched_game_id==watch.id))
        return ok(request,watch_data(watch,job))


@router.get('/watches')
def watches(request: Request,limit:int=Query(100,ge=1,le=100)):
    with Session(request.app.state.engine) as db:
        items=list(db.scalars(select(WatchedGame).order_by(WatchedGame.watched_at.desc()).limit(limit)))
        return ok(request,{'items':[watch_data(w,db.scalar(select(Job).where(Job.watched_game_id==w.id))) for w in items]})


@router.get('/sync-schedules')
def schedules(request: Request):
    with Session(request.app.state.engine) as db:
        revision=db.get(ScheduleRevision,1)
        items=db.scalars(select(SyncSchedule).where(SyncSchedule.enabled.is_(True)).order_by(SyncSchedule.sport,SyncSchedule.utc_time))
        return ok(request,{'revision':revision.revision,'items':[{'id':str(x.id),'sport':x.sport,'provider':x.provider,'utc_time':x.utc_time.isoformat(),'enabled':x.enabled} for x in items]})


class ScheduleItem(BaseModel):
    sport:Literal['football','nba']
    provider:Literal['api_football','balldontlie']
    utc_time:time
    enabled:bool=True
    @model_validator(mode='after')
    def valid(self):
        if self.utc_time.tzinfo is not None or (self.sport,self.provider) not in (('football','api_football'),('nba','balldontlie')): raise ValueError('invalid schedule')
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
    provider='api_football' if body.sport=='football' else 'balldontlie'
    with Session(request.app.state.engine) as db, db.begin():
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:name))'),{'name':'manual-sync:'+provider})
        existing=db.scalar(select(Job).join(SyncRun,Job.sync_run_id==SyncRun.id).where(SyncRun.provider==provider,SyncRun.trigger_type=='manual',Job.status.in_(['pending','leased'])))
        if existing: return ok(request,{'run_id':str(existing.sync_run_id),'job_id':str(existing.id),'status':existing.status})
        now=db_now(db); run=SyncRun(id=uuid.uuid4(),provider=provider,sport=body.sport,trigger_type='manual',scheduled_for=now,status='queued'); db.add(run); db.flush()
        job=Job(id=uuid.uuid4(),kind='sync',dedupe_key='manual:'+str(run.id),sync_run_id=run.id,due_at=now,status='pending'); db.add(job)
        return ok(request,{'run_id':str(run.id),'job_id':str(job.id),'status':'queued'})


@router.get('/jobs')
def jobs(request: Request,kind:Literal['sync','result_poll','trade_reconcile','combo_reconcile'] | None=None,status:Literal['pending','leased','done','cancelled','failed'] | None=None,limit:int=Query(100,ge=1,le=100)):
    query=select(Job)
    if kind: query=query.where(Job.kind==kind)
    if status: query=query.where(Job.status==status)
    with Session(request.app.state.engine) as db:
        rows=db.scalars(query.order_by(Job.due_at,Job.id).limit(limit))
        return ok(request,{'items':[{'id':str(x.id),'kind':x.kind,'status':x.status,'due_at':x.due_at.isoformat(),'attempt_count':x.attempt_count,'last_error_code':x.last_error_code,'watched_game_id':str(x.watched_game_id) if x.watched_game_id else None} for x in rows]})


@router.get('/jobs/{job_id}/runs')
def job_runs(request:Request,job_id:uuid.UUID,limit:int=Query(30,ge=1,le=100)):
    with Session(request.app.state.engine) as db:
        if not db.get(Job,job_id): return auth.error(request,404,'NOT_FOUND','任务不存在')
        rows=db.scalars(select(JobRun).where(JobRun.job_id==job_id).order_by(JobRun.attempt_no.desc()).limit(limit))
        return ok(request,{'items':[{'attempt_no':x.attempt_no,'started_at':x.started_at.isoformat(),'finished_at':x.finished_at.isoformat() if x.finished_at else None,'result':x.result,'error_code':x.error_code} for x in rows]})


@router.get('/worker-status')
def worker_status(request:Request):
    with Session(request.app.state.engine) as db:
        now=db_now(db); rows=db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(20))
        return ok(request,{'workers':[{'id':str(x.worker_id),'state':x.state if (now-x.last_seen_at).total_seconds()<60 else 'unavailable','last_seen_at':x.last_seen_at.isoformat()} for x in rows]})
