from __future__ import annotations
from datetime import timedelta
from decimal import Decimal,InvalidOperation
from sqlalchemy import select
from sqlalchemy.orm import Session
from .models import MarketLink,MarketOutcome,PolyMarket,TradeIntent,TradeStateEvent,TradeSubmission
from .scheduler import db_now,finish_attempt
from .trading import wallet_for
from .trading_gateway import Gateway,TradingError
import uuid


def fetch_order(engine,intent_id,settings,gateway_cls=Gateway):
    with Session(engine) as db:
        intent=db.get(TradeIntent,intent_id)
        if not intent: raise TradingError('intent_missing','订单不存在')
        if not intent.external_order_id: return None
        wallet=wallet_for(settings,intent.wallet_id)
        if wallet.wallet_address!=intent.wallet_address: raise TradingError('wallet_mismatch','订单钱包已变化')
        order_id=intent.external_order_id
    with gateway_cls(settings,wallet) as gateway:
        return gateway.get_order(order_id)


def order_matches(intent,market,outcome,payload,order_id=None,require_time=False):
    try:
        matched=Decimal(str(payload.size_matched)); original=Decimal(str(payload.original_size)); price=Decimal(str(payload.price))
        valid=(str(payload.id)==(order_id or intent.external_order_id) and str(payload.asset_id)==outcome.asset_id and str(payload.condition_id).lower()==market.condition_id and str(payload.side).upper()==intent.side and str(payload.maker_address).lower()==intent.wallet_address and price==intent.limit_price and original==intent.requested_size and matched.is_finite() and 0<=matched<=intent.requested_size)
        if require_time:
            from datetime import datetime,timezone
            valid=valid and payload.created_at.tzinfo is not None and intent.created_at-timedelta(minutes=2)<=payload.created_at<=datetime.now(timezone.utc)+timedelta(minutes=1)
        return valid,matched
    except (AttributeError,InvalidOperation,ValueError,TypeError): return False,None


def apply_reconciliation(db,job,payload,error=None):
    intent=db.scalar(select(TradeIntent).where(TradeIntent.id==job.trade_intent_id).with_for_update())
    now=db_now(db)
    if not intent:
        finish_attempt(db,job,now,'failed','intent_missing');job.status='failed';return
    if intent.state=='submitting':
        intent.state='unknown';intent.error_code='submission_unknown';intent.updated_at=now
        submission=db.scalar(select(TradeSubmission).where(TradeSubmission.intent_id==intent.id).with_for_update())
        if submission and submission.state=='submitting': submission.state='unknown';submission.error_code='submission_unknown';submission.finished_at=now
        db.add(TradeStateEvent(id=uuid.uuid4(),intent_id=intent.id,state='unknown',code='submission_unknown'))
    if intent.cancel_state=='submitting': intent.cancel_state='unknown';intent.updated_at=now
    if payload is not None and not error:
        market_link=db.get(MarketLink,intent.market_link_id);market=db.get(PolyMarket,market_link.market_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        correct,matched=order_matches(intent,market,outcome,payload)
        if not correct: error='order_evidence_mismatch'
        else:
            status=str(payload.status).lower();old=intent.state
            stale=(intent.matched_size is not None and matched<intent.matched_size) or (intent.cancel_state=='confirmed' and status not in ('canceled','cancelled')) or (intent.state=='filled' and (status not in ('matched','filled') or matched!=intent.requested_size))
            if stale:
                finish_attempt(db,job,now,'failed','stale_order_snapshot');job.status='pending';job.due_at=now+timedelta(minutes=2);return
            intent.matched_size=matched
            if status in ('canceled','cancelled'):
                intent.state='cancelled';intent.cancel_state='confirmed'
            elif matched==intent.requested_size and status in ('matched','filled'):
                intent.state='filled'
            elif matched>0:
                intent.state='partially_filled'
            elif status in ('live','open','active','delayed'):
                intent.state='pending'
            if intent.state in ('pending','partially_filled','filled','cancelled'):
                from .result_binding import request_binding_for_accepted_order
                request_binding_for_accepted_order(db,intent.game_id)
            intent.updated_at=now
            if intent.state!=old: db.add(TradeStateEvent(id=uuid.uuid4(),intent_id=intent.id,state=intent.state))
    if error:
        finish_attempt(db,job,now,'failed',error);job.status='pending';job.due_at=now+timedelta(minutes=2)
    elif intent.state in ('filled','rejected','cancelled') and intent.cancel_state!='unknown':
        finish_attempt(db,job,now,'succeeded');job.status='done'
    else:
        finish_attempt(db,job,now,'succeeded');job.status='pending';job.due_at=now+timedelta(minutes=2)
