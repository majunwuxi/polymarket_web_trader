"""可赎回资格查询与整仓赎回入口。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import auth
from .redeem import eligibility, redeem_combo, redeem_single
from .trading_gateway import TradingError

router = APIRouter(prefix='/api/v1')


class EligibilityBody(BaseModel):
    intent_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    combo_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


def ok(request, data):
    return {'data': data, 'request_id': request.state.request_id}


def failure(request, exc):
    status = (404 if exc.code == 'not_found'
              else 403 if exc.code == 'geoblock_blocked'
              else 409 if exc.code in ('redeem_not_available', 'redeem_in_progress',
                                       'redeem_result_unknown', 'redeem_approval_required')
              else 422 if exc.code == 'invalid_amount'
              else 503)
    return auth.error(request, status, exc.code.upper(), exc.message, delay=exc.retry_after)


@router.post('/redemptions/eligibility')
def redeem_eligibility(request: Request, body: EligibilityBody):
    try:
        return ok(request, eligibility(request.app.state.engine, request.app.state.settings,
                                       body.intent_ids, body.combo_ids))
    except TradingError as exc:
        return failure(request, exc)


def _execute(request, kind, ident, idempotency_key):
    try:
        request_id = uuid.UUID(idempotency_key)
    except (ValueError, TypeError):
        return auth.error(request, 422, 'VALIDATION_ERROR', '幂等键必须是 UUID')
    settings = request.app.state.settings
    if not settings.redeem_execution_enabled:
        return auth.error(request, 503, 'REDEEM_DISABLED', '赎回功能未开启')
    try:
        actor_id = request.state.admin_session['admin_id']
        correlation_id = uuid.UUID(request.state.request_id)
        operation = redeem_single if kind == 'single' else redeem_combo
        data, status = operation(request.app.state.engine, settings, ident, request_id,
                                 actor_id, correlation_id)
        if status == 409:
            return auth.error(request, 409, 'REDEEM_REJECTED', data.get('message') or '赎回请求未发送')
        return JSONResponse(status_code=status, content=ok(request, data))
    except TradingError as exc:
        return failure(request, exc)


@router.post('/trade-intents/{intent_id}/redeem')
def redeem_trade_intent(request: Request, intent_id: uuid.UUID,
                        idempotency_key: str = Header(..., alias='Idempotency-Key')):
    return _execute(request, 'single', intent_id, idempotency_key)


@router.post('/combo-rfqs/{combo_id}/redeem')
def redeem_combo_rfq(request: Request, combo_id: uuid.UUID,
                     idempotency_key: str = Header(..., alias='Idempotency-Key')):
    return _execute(request, 'combo', combo_id, idempotency_key)
