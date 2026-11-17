from datetime import timedelta
import uuid
from polymarket import RequestRejectedError
from sqlalchemy import select
from sqlalchemy.orm import Session
from .close_positions import combo_position_balance
from .combo_gateway import ComboGateway
from .combo_service import record, rfq_failure_code
from .models import ComboRFQ, ComboLeg, ComboEvent, ComboFill
from .scheduler import db_now, finish_attempt, retry_or_fail
from .trading import wallet_for
from .trading_gateway import TradingError

MAX_COMBO_PENDING_CHECKS = 120


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
    finish_attempt(db,job,now,'succeeded');job.status='done';job.failure_count=0
    job.last_error_code=job.last_error_summary=None
    return True


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
            retry_or_fail(db,job,now,error,timedelta(minutes=2))
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
