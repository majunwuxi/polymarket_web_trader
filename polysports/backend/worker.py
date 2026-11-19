from __future__ import annotations

import logging
import signal
import threading
import time
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .database import make_engine
from .models import Game, GameRevision, Job, ResultBindingRequest, SyncRun, WatchedGame
from .scheduler import claim_next, db_now, ensure_scheduled, finish_attempt, first_delay, heartbeat, interval, locked_valid_job, renew, retry_or_fail
from .sports_sources import SourceError, fetch_game, fetch_schedule
from .sports_sync import upsert_game, apply_polymarket_scan
from .trading_gateway import TradingError

log=logging.getLogger('polysports.worker')
IDLE_SECONDS=15
HEARTBEAT_SECONDS=15
SCHEDULE_SECONDS=60


class LeaseLost(Exception):
    pass


def fetch_work(engine,job_id,token,settings,client):
    with Session(engine) as db:
        job=db.get(Job,job_id)
        if not job or job.status!='leased' or job.lease_token!=token: raise LeaseLost()
        if job.kind in ('cleanup','combo_watchdog'): return None
        if job.kind=='sync':
            run=db.get(SyncRun,job.sync_run_id); provider=run.provider; start=run.window_start; sport=run.sport
        elif job.kind=='combo_reconcile':
            combo_id=job.combo_rfq_id
        elif job.kind=='trade_reconcile':
            from .trade_reconcile import fetch_order
            trade_id=job.trade_intent_id
        else:
            watched=db.get(WatchedGame,job.watched_game_id)
            if watched.state!='active': return None
            game=db.get(Game,watched.game_id)
            if game.status=='finished' and game.home_score is not None and game.away_score is not None: return None
            provider=game.provider; external_id=game.external_id
            if provider=='polymarket':
                binding=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game.id,ResultBindingRequest.state=='bound'))
                if not binding or not binding.fixture_id: raise SourceError('binding_required')
                provider=binding.provider;external_id=binding.fixture_id
    if job.kind=='combo_reconcile':
        from .combo_reconcile import fetch_combo
        return fetch_combo(engine,combo_id,settings)
    if job.kind=='trade_reconcile': return fetch_order(engine,trade_id,settings)
    if provider=='polymarket':
        if job.kind=='result_poll': raise SourceError('binding_required')
        if sport=='football':
            from .polymarket_football import discover_football
            return discover_football(client,start)
        if sport=='nba':
            from .polymarket_nba import discover_nba
            return discover_nba(client,start)
        raise SourceError('invalid_sport')
    secret=settings.api_football_key if provider=='api_football' else settings.balldontlie_key
    key=secret.get_secret_value() if secret else ''
    if job.kind=='result_poll': return fetch_game(provider,key,external_id,client)
    games,pages,received=fetch_schedule(provider,key,start,client)
    if provider=='balldontlie':
        extra,p2,r2=fetch_schedule(provider,key,start,client,season_type='preseason')
        unique={x.external_id:x for x in games}
        for game in extra:
            if game.external_id in unique and unique[game.external_id]!=game: raise SourceError('conflicting_fixture')
            unique[game.external_id]=game
        games,pages,received=list(unique.values()),pages+p2,received+r2
    return games,pages,received


def apply_work(engine,job_id,token,payload=None,error: str | None=None,retry_after_seconds: int=0,settings=None) -> bool:
    try:
        with Session(engine) as db, db.begin():
            job=locked_valid_job(db,job_id,token)
            if not job: return False
            now=db_now(db)
            lease_expiry=job.lease_until
            if job.kind=='sync':
                run=db.get(SyncRun,job.sync_run_id)
                if error:
                    run.status='failed'; run.error_code=error; run.error_summary='来源暂不可用'; run.finished_at=now
                    finish_attempt(db,job,now,'failed',error)
                    job.status='pending'; job.due_at=now+max(timedelta(minutes=10),timedelta(seconds=retry_after_seconds))
                else:
                    games,pages,received=payload
                    db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:provider))'),{'provider':run.provider})
                    if run.provider=='polymarket': apply_polymarket_scan(db,games,run)
                    else:
                        for game in games: upsert_game(db,game,run.id)
                    run.status='succeeded'; run.pages_fetched=pages; run.items_received=received; run.items_upserted=len(games); run.finished_at=now; run.error_code=run.error_summary=None
                    finish_attempt(db,job,now,'succeeded'); job.status='done'
            elif job.kind=='combo_reconcile':
                from .combo_reconcile import apply_combo
                apply_combo(db,job,payload,error,settings=settings)
            elif job.kind=='trade_reconcile':
                from .trade_reconcile import apply_reconciliation
                apply_reconciliation(db,job,payload,error)
            elif job.kind=='cleanup':
                if error:
                    retry_or_fail(db,job,now,error,timedelta(hours=1))
                else:
                    from .maintenance import cleanup_expired
                    counts=cleanup_expired(db)
                    finish_attempt(db,job,now,'succeeded');job.status='done'
                    log.info('maintenance_cleanup admin_sessions=%s auth_rate_limits=%s audit_events=%s',counts['admin_sessions'],counts['auth_rate_limits'],counts['audit_events'])
            elif job.kind=='combo_watchdog':
                if error:
                    retry_or_fail(db,job,now,error,timedelta(minutes=5))
                else:
                    from .combo_reconcile import run_combo_watchdog
                    counts=run_combo_watchdog(db,settings,now)
                    finish_attempt(db,job,now,'succeeded');job.status='done'
                    log.info('combo_watchdog scanned=%s confirmed=%s closed=%s',counts['scanned'],counts['confirmed'],counts['closed'])
            else:
                watch=db.scalar(select(WatchedGame).where(WatchedGame.id==job.watched_game_id).with_for_update())
                game=db.scalar(select(Game).where(Game.id==watch.game_id).with_for_update())
                if watch.state!='active' or (game.status=='finished' and game.home_score is not None and game.away_score is not None):
                    finish_attempt(db,job,now,'succeeded'); job.status='done'
                elif error:
                    finish_attempt(db,job,now,'failed',error); job.status='pending'; job.due_at=now+max(interval(game.sport),timedelta(seconds=retry_after_seconds))
                else:
                    # 严格防止错误的来源记录关联至被关注赛事。
                    if game.provider=='polymarket':
                        binding=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game.id,ResultBindingRequest.state=='bound'))
                        if not binding or payload.provider!=binding.provider or payload.external_id!=binding.fixture_id: raise SourceError('fixture_mismatch')
                        if payload.status=='finished' and (payload.home_score is None or payload.away_score is None): raise SourceError('final_score_missing')
                    elif payload.provider!=game.provider or payload.external_id!=game.external_id: raise SourceError('fixture_mismatch')
                    old_start=game.starts_at; first=watch.last_checked_at is None
                    if game.provider=='polymarket':
                        if (game.starts_at,game.status,game.home_score,game.away_score)!=(payload.starts_at,payload.status,payload.home_score,payload.away_score):
                            db.add(GameRevision(id=uuid.uuid4(),game_id=game.id,old_starts_at=game.starts_at,new_starts_at=payload.starts_at,old_status=game.status,new_status=payload.status,provider_status=payload.provider_status,home_score=payload.home_score,away_score=payload.away_score,change_reason='verified_result'))
                        game.starts_at=payload.starts_at;game.status=payload.status;game.provider_status=payload.provider_status
                        game.home_score=payload.home_score;game.away_score=payload.away_score;game.updated_at=now
                    else: upsert_game(db,payload,None,reschedule=False)
                    watch.last_checked_at=now; watch.updated_at=now
                    if old_start!=game.starts_at:
                        watch.first_due_at=game.starts_at+first_delay(game.sport)
                    finish_attempt(db,job,now,'succeeded')
                    if game.status=='finished':
                        watch.state='completed'; watch.completed_at=now; job.status='done'
                    else:
                        job.status='pending'; job.due_at=max(now+interval(game.sport),watch.first_due_at) if first or old_start!=game.starts_at else now+interval(game.sport)
            # 持有行锁时再次按数据库时钟验证，超时则整个业务事务回滚。
            if lease_expiry <= db_now(db): raise LeaseLost()
        return True
    except LeaseLost:
        return False


def process_one(engine,settings,owner,client=None,lease_seconds=120) -> bool:
    claimed=claim_next(engine,owner,lease_seconds)
    if not claimed: return False
    job_id,token=claimed
    stop=threading.Event()
    def renew_loop():
        while not stop.wait(max(1,lease_seconds/3)):
            try:
                if not renew(engine,job_id,token,lease_seconds): return
                try: heartbeat(engine,uuid.UUID(owner),'running')
                except ValueError: pass
            except Exception:
                log.exception('lease_renewal_failed')
                return
    thread=threading.Thread(target=renew_loop,daemon=True); thread.start()
    owned=client is None; source_client=client or httpx.Client()
    try:
        try:
            payload=fetch_work(engine,job_id,token,settings,source_client)
            apply_work(engine,job_id,token,payload,settings=settings)
        except (SourceError,TradingError) as exc:
            apply_work(engine,job_id,token,error=exc.code,retry_after_seconds=getattr(exc,'retry_after_seconds',0),settings=settings)
        except LeaseLost:
            pass
        except Exception as exc:
            log.exception('job_failed type=%s job_id=%s error=%s',type(exc).__name__,job_id,str(exc)[:300])
            apply_work(engine,job_id,token,error='internal_error',settings=settings)
    finally:
        stop.set(); thread.join(timeout=5)
        if owned: source_client.close()
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    settings=get_settings(); engine=make_engine(settings); worker_id=uuid.uuid4(); stopping=threading.Event()
    def stop(*_): stopping.set()
    signal.signal(signal.SIGINT,stop); signal.signal(signal.SIGTERM,stop)
    last_heartbeat=last_schedule=-float('inf')
    client=httpx.Client()
    try:
        while not stopping.is_set():
            try:
                now=time.monotonic()
                if now-last_heartbeat>=HEARTBEAT_SECONDS:
                    heartbeat(engine,worker_id,'running'); last_heartbeat=now
                if now-last_schedule>=SCHEDULE_SECONDS:
                    try: ensure_scheduled(engine)
                    except Exception: log.exception('scheduler_failed')
                    last_schedule=now
                worked=process_one(engine,settings,str(worker_id),client=client)
                from .result_binding import process_pending_binding
                bound=process_pending_binding(engine,settings,client)
                if not worked and not bound: stopping.wait(IDLE_SECONDS)
            except Exception as exc:
                log.exception('worker_iteration_failed type=%s',type(exc).__name__); stopping.wait(5)
    finally:
        client.close()
        try: heartbeat(engine,worker_id,'stopping')
        finally: engine.dispose()

if __name__=='__main__': main()
