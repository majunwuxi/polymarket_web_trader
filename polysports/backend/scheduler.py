from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, func
from sqlalchemy.orm import Session

from .models import Game, Job, JobRun, SyncRun, SyncSchedule, WatchedGame, WorkerHeartbeat


def db_now(db: Session) -> datetime:
    return db.scalar(select(func.clock_timestamp()))


def interval(sport: str) -> timedelta:
    return timedelta(minutes=10 if sport == 'football' else 3)


def first_delay(sport: str) -> timedelta:
    return timedelta(minutes=120 if sport == 'football' else 159)


def watch_game(db: Session, game_id: uuid.UUID) -> WatchedGame | None:
    game = db.scalar(select(Game).where(Game.id==game_id).with_for_update())
    if game is None: return None
    watch = db.scalar(select(WatchedGame).where(WatchedGame.game_id==game_id))
    if watch is not None: return watch
    now = db_now(db)
    done = game.status == 'finished'
    watch = WatchedGame(id=uuid.uuid4(),game_id=game.id,state='completed' if done else 'active',first_due_at=game.starts_at+first_delay(game.sport),completed_at=now if done else None)
    db.add(watch); db.flush()
    if not done:
        db.add(Job(id=uuid.uuid4(),kind='result_poll',dedupe_key='watch:'+str(watch.id),watched_game_id=watch.id,due_at=watch.first_due_at,status='pending'))
    return watch


def abandon_lease(db: Session, job: Job, now: datetime) -> None:
    if job.status == 'leased':
        run = db.scalar(select(JobRun).where(JobRun.job_id==job.id,JobRun.attempt_no==job.attempt_count,JobRun.result=='running').with_for_update())
        if run:
            run.result='abandoned'; run.finished_at=now
    job.lease_owner=job.lease_until=job.lease_token=None


def reschedule_watch(db: Session, game: Game, old_start: datetime) -> None:
    watch = db.scalar(select(WatchedGame).where(WatchedGame.game_id==game.id).with_for_update())
    if not watch or watch.state != 'active': return
    job = db.scalar(select(Job).where(Job.watched_game_id==watch.id).with_for_update())
    if not job or job.status not in ('pending','leased'): return
    now=db_now(db)
    if game.status == 'finished':
        abandon_lease(db,job,now)
        watch.state='completed'; watch.completed_at=now; watch.updated_at=now
        job.status='done'; job.updated_at=now
    elif game.starts_at != old_start:
        abandon_lease(db,job,now)
        watch.first_due_at=game.starts_at+first_delay(game.sport); watch.updated_at=now
        job.status='pending'; job.due_at=watch.first_due_at if watch.last_checked_at is None else max(watch.first_due_at,now+interval(game.sport)); job.updated_at=now


def ensure_scheduled(engine) -> None:
    with Session(engine) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('polysports-scheduler'))"))
        now=db_now(db)
        schedules=list(db.scalars(select(SyncSchedule).where(SyncSchedule.enabled.is_(True))))
        for schedule in schedules:
            for day in (now.date(), now.date()+timedelta(days=1), now.date()+timedelta(days=2)):
                due=datetime.combine(day,schedule.utc_time,tzinfo=timezone.utc)
                if due < now: continue
                run=db.scalar(select(SyncRun).where(SyncRun.schedule_id==schedule.id,SyncRun.scheduled_for==due))
                if run is None:
                    run=SyncRun(id=uuid.uuid4(),schedule_id=schedule.id,provider=schedule.provider,sport=schedule.sport,trigger_type='scheduled',scheduled_for=due,status='queued')
                    db.add(run); db.flush()
                    db.add(Job(id=uuid.uuid4(),kind='sync',dedupe_key='sync:'+str(schedule.id)+':'+due.isoformat(),schedule_id=schedule.id,sync_run_id=run.id,due_at=due,status='pending'))
                else:
                    job=db.scalar(select(Job).where(Job.sync_run_id==run.id))
                    if job and job.status=='cancelled' and job.attempt_count==0:
                        job.status='pending'; run.status='queued'; run.error_code=run.error_summary=None; run.finished_at=None


def claim_next(engine, owner: str, lease_seconds: int = 120) -> tuple[uuid.UUID,uuid.UUID] | None:
    with Session(engine) as db, db.begin():
        now=db_now(db)
        expired=list(db.scalars(select(Job).where(Job.status=='leased',Job.lease_until<=now).with_for_update(skip_locked=True)))
        for job in expired:
            abandon_lease(db,job,now); job.status='pending'; job.updated_at=now
            if job.kind=='sync':
                run=db.get(SyncRun,job.sync_run_id)
                if run: run.status='queued'
        db.flush()
        job=db.scalar(select(Job).where(Job.status=='pending',Job.due_at<=now).order_by(Job.due_at,Job.id).with_for_update(skip_locked=True).limit(1))
        if job is None: return None
        token=uuid.uuid4(); job.status='leased'; job.lease_token=token; job.lease_owner=owner; job.lease_until=now+timedelta(seconds=lease_seconds); job.attempt_count+=1; job.updated_at=now
        db.add(JobRun(id=uuid.uuid4(),job_id=job.id,attempt_no=job.attempt_count,lease_token=token,started_at=now,result='running'))
        if job.kind=='sync':
            run=db.get(SyncRun,job.sync_run_id); run.status='running'; run.started_at=now; run.window_start=now; run.window_end=now+timedelta(hours=36)
        return job.id,token


def renew(engine, job_id: uuid.UUID, token: uuid.UUID, lease_seconds: int = 120) -> bool:
    with Session(engine) as db, db.begin():
        result=db.execute(text("UPDATE jobs SET lease_until=clock_timestamp()+make_interval(secs => :seconds),updated_at=clock_timestamp() WHERE id=:id AND status='leased' AND lease_token=:token AND lease_until>clock_timestamp()"),{'seconds':lease_seconds,'id':job_id,'token':token})
        return result.rowcount==1


def locked_valid_job(db: Session, job_id: uuid.UUID, token: uuid.UUID) -> Job | None:
    job=db.scalar(select(Job).where(Job.id==job_id).with_for_update())
    if job is None or job.status!='leased' or job.lease_token!=token or job.lease_until <= db_now(db): return None
    return job


def finish_attempt(db: Session, job: Job, now: datetime, result: str, error: str | None = None) -> None:
    attempt=db.scalar(select(JobRun).where(JobRun.job_id==job.id,JobRun.attempt_no==job.attempt_count,JobRun.lease_token==job.lease_token).with_for_update())
    if not attempt or attempt.result!='running': raise RuntimeError('missing active attempt')
    attempt.result=result; attempt.finished_at=now; attempt.error_code=error; attempt.error_summary='来源暂不可用' if error else None
    job.last_error_code=error; job.last_error_summary='来源暂不可用' if error else None; job.updated_at=now
    job.lease_owner=job.lease_until=job.lease_token=None


def heartbeat(engine, worker_id: uuid.UUID, state: str) -> None:
    with Session(engine) as db, db.begin():
        now=db_now(db); obj=db.get(WorkerHeartbeat,worker_id)
        if obj is None: db.add(WorkerHeartbeat(worker_id=worker_id,started_at=now,last_seen_at=now,worker_version='6D',state=state))
        else: obj.last_seen_at=now; obj.state=state
