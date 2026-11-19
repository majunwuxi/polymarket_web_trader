from datetime import timedelta
import uuid
from polymarket import RequestRejectedError
from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session
from .close_policy import score_verified_clause
from .close_positions import combo_position_balance
from .combo_gateway import ComboGateway
from .combo_service import record, rfq_failure_code
from .models import ComboRFQ, ComboLeg, ComboEvent, ComboFill, Game, Job, MarketLink, PolyMarket
from .scheduler import MAX_CONSECUTIVE_FAILURES, db_now, finish_attempt, retry_or_fail
from .trading import wallet_for
from .trading_gateway import TradingError

MAX_COMBO_PENDING_CHECKS = 120
POSITION_CLOSED_SCAN_HOURS = 24 * 7


def fetch_combo(engine,ident,settings,gateway_cls=ComboGateway):
    with Session(engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:raise TradingError('combo_missing','组合询价不存在')
        if not row.external_rfq_id or row.state in ('requesting','quoted'):return None
        wallet=wallet_for(settings,row.wallet_id)
        if wallet.wallet_address!=row.wallet_address:raise TradingError('wallet_mismatch','钱包已变化')
        external=row.external_rfq_id; mode=row.gateway_mode
    try:
        if gateway_cls is ComboGateway:
            with gateway_cls(settings,wallet,mode=mode) as gateway:return gateway.client.fetch_rfq_status(rfq_id=external)
        with gateway_cls(settings,wallet) as gateway:return gateway.client.fetch_rfq_status(rfq_id=external)
    except RequestRejectedError as exc:
        if exc.status==404: raise TradingError('rfq_not_found','上游已找不到该组合 RFQ，请到 Polymarket 核对持仓和成交记录') from exc
        raise


def _confirm_from_chain(db,row,job,settings,now):
    """When the RFQ API loses the request, verify the recorded combo position on-chain."""
    if settings is None or not getattr(settings,'polygon_rpc_url',None): return False
    if row.direction!='BUY' or not row.combo_position_id: return False
    fill=db.scalar(select(ComboFill).where(ComboFill.rfq_id==row.id).with_for_update())
    if not fill or fill.state!='pending' or fill.size<=0: return False
    try: balance=combo_position_balance(settings,row.wallet_address,row.combo_position_id)
    except TradingError: return False
    if balance<fill.size: return False
    row.state='confirmed';row.error_code=None;row.updated_at=now
    db.add(ComboEvent(id=uuid.uuid4(),rfq_id=row.id,state='confirmed',code='onchain_position_verified'))
    from .combo_fills import confirm_combo_fill
    confirm_combo_fill(db,row,now)
    from .result_binding import request_binding_for_accepted_order
    game_ids=set(db.scalars(select(ComboLeg.game_id).where(ComboLeg.rfq_id==row.id)))
    for game_id in sorted(game_ids,key=str): request_binding_for_accepted_order(db,game_id)
    if job is not None:
        finish_attempt(db,job,now,'succeeded');job.status='done';job.failure_count=0
        job.last_error_code=job.last_error_summary=None
    return True


def _combo_unsettled():
    """任一腿赛果已确认或市场已停单时不再视为未结算，避免把结算清仓误判为手动平仓。"""
    return ~exists(select(ComboLeg.id).join(Game,Game.id==ComboLeg.game_id).join(
        MarketLink,MarketLink.id==ComboLeg.market_link_id).join(
        PolyMarket,PolyMarket.id==MarketLink.market_id).where(
        ComboLeg.rfq_id==ComboRFQ.id,
        or_(score_verified_clause(Game),PolyMarket.accepting_orders.is_(False))))


def mark_closed_positions(db,settings,now,limit=5):
    """核对已确认组合的链上持仓：余额为 0 且尚未结算即视为已在 Polymarket 手动平仓。

    每轮最多检查 limit 条并按 updated_at 轮转，避免重复调用 RPC；RPC 不可用时不做推断。
    """
    rows=list(db.scalars(select(ComboRFQ).where(
        ComboRFQ.state=='confirmed',
        ComboRFQ.direction=='BUY',
        ComboRFQ.combo_position_id.is_not(None),
        ComboRFQ.position_closed_at.is_(None),
        ComboRFQ.created_at>=now-timedelta(hours=POSITION_CLOSED_SCAN_HOURS),
        _combo_unsettled(),
    ).order_by(ComboRFQ.updated_at,ComboRFQ.id).limit(limit).with_for_update(skip_locked=True)))
    closed=0
    for row in rows:
        row.updated_at=now
        try: balance=combo_position_balance(settings,row.wallet_address,row.combo_position_id)
        except TradingError: continue
        if balance>0: continue
        row.position_closed_at=now
        db.add(ComboEvent(id=uuid.uuid4(),rfq_id=row.id,state='closed',code='position_closed'))
        closed+=1
    return closed


def run_combo_watchdog(db,settings,now,limit=20):
    """Recheck on-chain balances for unresolved combos for up to 24 hours."""
    pending=exists(select(ComboFill.id).where(ComboFill.rfq_id==ComboRFQ.id,ComboFill.state=='pending'))
    rows=list(db.scalars(select(ComboRFQ).where(
        ComboRFQ.state.in_(('executing','unknown')),
        ComboRFQ.direction=='BUY',
        ComboRFQ.combo_position_id.is_not(None),
        ComboRFQ.created_at>=now-timedelta(hours=24),
        pending,
    ).order_by(ComboRFQ.updated_at,ComboRFQ.id).limit(limit).with_for_update(skip_locked=True)))
    confirmed=0
    for row in rows:
        if not _confirm_from_chain(db,row,None,settings,now): continue
        confirmed+=1
        job=db.scalar(select(Job).where(Job.combo_rfq_id==row.id).with_for_update())
        if job and job.status!='leased':
            job.status='done';job.failure_count=0
            job.last_error_code=job.last_error_summary=None
            job.lease_owner=job.lease_until=job.lease_token=None
    closed=mark_closed_positions(db,settings,now)
    return {'scanned':len(rows),'confirmed':confirmed,'closed':closed}


def apply_combo(db,job,payload,error=None,settings=None):
    row=db.scalar(select(ComboRFQ).where(ComboRFQ.id==job.combo_rfq_id).with_for_update());now=db_now(db)
    if not row:finish_attempt(db,job,now,'failed','combo_missing');job.status='failed';return
    if row.state=='requesting':record(db,row,'unknown','request_unknown')
    if row.state=='quoted' and row.quote_expires_at and row.quote_expires_at<=now:record(db,row,'expired','quote_expired')
    if row.state=='accepting':record(db,row,'unknown','accept_unknown')
    if payload is not None and not error:
        if str(payload.rfq_id)!=row.external_rfq_id:error='rfq_mismatch'
        else:
            status=str(payload.status).upper().split('.')[-1]
            target={'CONFIRMED':'confirmed','FILLED':'confirmed','FAILED':'failed','EXPIRED':'expired','CANCELED':'rejected','AWAITING_REQUESTER_ACCEPTANCE':'executing','AWAITING_MAKER_CONFIRMATION':'executing','EXECUTING':'executing','MATCHED':'executing','MINED':'executing','RETRYING':'executing'}.get(status)
            if not target:error='rfq_status_unknown'
            elif row.state not in ('confirmed','failed','expired','rejected'):
                if target!=row.state:
                    code=rfq_failure_code(payload,fallback='rfq_'+target) if target in ('failed','expired','rejected') else None
                    record(db,row,target,code)
                if target=='confirmed':
                    if getattr(payload,'tx_hash',None):row.tx_hash=str(payload.tx_hash)
                    from .combo_fills import confirm_combo_fill
                    confirm_combo_fill(db,row,now)
                    from .result_binding import request_binding_for_accepted_order
                    game_ids=set(db.scalars(select(ComboLeg.game_id).where(ComboLeg.rfq_id==row.id)))
                    for game_id in sorted(game_ids,key=str): request_binding_for_accepted_order(db,game_id)
                elif target in ('failed','expired','rejected'):
                    from .combo_fills import void_combo_fill
                    void_combo_fill(db,row)
            elif row.state=='confirmed':
                # 迁移之外的兜底：历史组合已确认但尚未回填明细时，按签名报价补齐。
                from .combo_fills import confirm_combo_fill
                confirm_combo_fill(db,row,now)
    if error:
        if error=='rfq_not_found':
            if _confirm_from_chain(db,row,job,settings,now): return
            if row.state not in ('confirmed','failed','expired','rejected'):
                row.state='unknown';row.error_code='rfq_not_found';row.updated_at=now
                db.add(ComboEvent(id=uuid.uuid4(),rfq_id=row.id,state='unknown',code='rfq_not_found'))
            finish_attempt(db,job,now,'failed',error)
            job.status='failed';job.last_error_code=error
            job.last_error_summary='上游已找不到该组合 RFQ，已停止自动重试'
        else:
            next_failures=(job.failure_count or 0)+1
            if next_failures>=MAX_CONSECUTIVE_FAILURES and _confirm_from_chain(db,row,job,settings,now): return
            retry_or_fail(db,job,now,error,timedelta(minutes=2))
            if job.status=='failed' and row.state not in ('confirmed','failed','expired','rejected'):
                row.state='unknown';row.error_code='reconcile_failed';row.updated_at=now
                db.add(ComboEvent(id=uuid.uuid4(),rfq_id=row.id,state='unknown',code='reconcile_failed'))
    elif row.state in ('confirmed','failed','expired','rejected'):
        finish_attempt(db,job,now,'succeeded');job.status='done'
    else:
        next_failures=(job.failure_count or 0)+1
        if next_failures>=MAX_COMBO_PENDING_CHECKS and _confirm_from_chain(db,row,job,settings,now): return
        finish_attempt(db,job,now,'succeeded',reset_failures=False)
        job.failure_count=next_failures
        if next_failures>=MAX_COMBO_PENDING_CHECKS:
            if row.state not in ('confirmed','failed','expired','rejected'):
                row.state='unknown';row.error_code='reconcile_stale';row.updated_at=now
                db.add(ComboEvent(id=uuid.uuid4(),rfq_id=row.id,state='unknown',code='reconcile_stale'))
            job.status='failed'
            job.last_error_code='reconcile_stale'
            job.last_error_summary=f'组合对账 {next_failures} 次仍未到终态，已停止自动重试'
        else:
            job.status='pending';job.due_at=now+timedelta(minutes=2)
