from datetime import timedelta
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session
from .combo_gateway import ComboGateway
from .combo_service import record
from .models import ComboRFQ, ComboLeg
from .scheduler import db_now, finish_attempt
from .trading import wallet_for
from .trading_gateway import TradingError


def fetch_combo(engine,ident,settings,gateway_cls=ComboGateway):
    with Session(engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:raise TradingError('combo_missing','组合询价不存在')
        if not row.external_rfq_id or row.state in ('requesting','quoted'):return None
        wallet=wallet_for(settings,row.wallet_id)
        if wallet.wallet_address!=row.wallet_address:raise TradingError('wallet_mismatch','钱包已变化')
        external=row.external_rfq_id; mode=row.gateway_mode
    if gateway_cls is ComboGateway:
        with gateway_cls(settings,wallet,mode=mode) as gateway:return gateway.client.fetch_rfq_status(rfq_id=external)
    with gateway_cls(settings,wallet) as gateway:return gateway.client.fetch_rfq_status(rfq_id=external)


def apply_combo(db,job,payload,error=None):
    row=db.scalar(select(ComboRFQ).where(ComboRFQ.id==job.combo_rfq_id).with_for_update());now=db_now(db)
    if not row:finish_attempt(db,job,now,'failed','combo_missing');job.status='failed';return
    if row.state=='requesting':record(db,row,'unknown','request_unknown')
    if row.state=='quoted' and row.quote_expires_at and row.quote_expires_at<=now:record(db,row,'expired','quote_expired')
    if row.state=='accepting':record(db,row,'unknown','accept_unknown')
    if payload is not None and not error:
        if str(payload.rfq_id)!=row.external_rfq_id:error='rfq_mismatch'
        else:
            status=str(payload.status).upper().split('.')[-1]
            target={'CONFIRMED':'confirmed','FILLED':'confirmed','FAILED':'failed','EXPIRED':'expired','CANCELED':'rejected','AWAITING_MAKER_CONFIRMATION':'executing','EXECUTING':'executing','MINED':'executing','RETRYING':'executing'}.get(status)
            if not target:error='rfq_status_unknown'
            elif row.state not in ('confirmed','failed','expired','rejected'):
                if target!=row.state:record(db,row,target)
                if target=='confirmed':
                    if getattr(payload,'tx_hash',None):row.tx_hash=str(payload.tx_hash)
                    from .result_binding import request_binding_for_accepted_order
                    game_ids=set(db.scalars(select(ComboLeg.game_id).where(ComboLeg.rfq_id==row.id)))
                    for game_id in sorted(game_ids,key=str): request_binding_for_accepted_order(db,game_id)
    if error:
        finish_attempt(db,job,now,'failed',error);job.status='pending';job.due_at=now+timedelta(minutes=2)
    elif row.state in ('confirmed','failed','expired','rejected'):
        finish_attempt(db,job,now,'succeeded');job.status='done'
    else:
        finish_attempt(db,job,now,'succeeded');job.status='pending';job.due_at=now+timedelta(minutes=2)
