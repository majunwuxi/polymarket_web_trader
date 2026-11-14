"""组合腿只读检查；询价和接受入口尚未开放。"""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from . import auth
from .combo_catalog import fetch_positions
from .markets import valid_link
from .models import MarketLink, MarketOutcome, PolyMarket
from .trading_gateway import TradingError

router = APIRouter(prefix='/api/v1')
# 组合资金操作仍须由部署环境显式启用。
COMBO_EXECUTION_READY = True


@router.get('/combo-legs/{link_id}/{outcome_id}')
def check_leg(request: Request, link_id: uuid.UUID, outcome_id: uuid.UUID):
    try:
        with Session(request.app.state.engine) as db:
            link = db.get(MarketLink, link_id)
            outcome = db.get(MarketOutcome, outcome_id)
            if not link or not outcome or link.market_id != outcome.market_id or not valid_link(db, link):
                return auth.error(request, 409, 'MAPPING_INVALID', '赛事映射未确认或已过期')
            market = db.get(PolyMarket, link.market_id)
            # 返回值只说明目录身份，不说明钱包、Builder 权限或交易可用性。
            positions = fetch_positions([market], [outcome])
            db.expire_all()
            current = db.get(MarketLink, link_id)
            if not current or not valid_link(db, current):
                return auth.error(request, 409, 'MAPPING_INVALID', '赛事映射在查询期间改变')
            return {'data': {'market_link_id': str(link_id), 'outcome_id': str(outcome_id),
                             'position_id': positions[outcome_id], 'builder_access': 'unknown',
                             'trading_available': False}, 'request_id': request.state.request_id}
    except TradingError as exc:
        return auth.error(request, 503, exc.code.upper(), exc.message)
from typing import Literal
from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictStr
from sqlalchemy import select, tuple_
from datetime import datetime
from .sports_api import cursor_decode, cursor_encode
from .combo_service import request_quote, accept_quote
from .models import ComboRFQ, ComboLeg, ComboEvent, Game, Team


class ComboLegBody(BaseModel):
    market_link_id: uuid.UUID
    outcome_id: uuid.UUID


class ComboBody(BaseModel):
    wallet_id: uuid.UUID
    direction: Literal['BUY','SELL']
    quantity: StrictStr = Field(min_length=1,max_length=64)
    legs: list[ComboLegBody] = Field(min_length=2,max_length=50)


class AcceptBody(BaseModel):
    quote_id: uuid.UUID


def combo_data(db,row):
    legs=list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==row.id)))
    details=[]
    for leg in legs:
        game=db.get(Game,leg.game_id); link=db.get(MarketLink,leg.market_link_id); market=db.get(PolyMarket,link.market_id); outcome=db.get(MarketOutcome,leg.outcome_id); home=db.get(Team,game.home_team_id); away=db.get(Team,game.away_team_id)
        details.append({'game_id':str(leg.game_id),'market_link_id':str(leg.market_link_id),'outcome_id':str(leg.outcome_id),'position_id':leg.position_id,'home':home.name_zh or home.source_name,'away':away.name_zh or away.source_name,'starts_at':game.starts_at.isoformat(),'market_question':market.question,'outcome_label':outcome.label})
    return {'id':str(row.id),'wallet_id':str(row.wallet_id),'wallet_address':row.wallet_address,'direction':row.direction,'quantity':str(row.quantity),'state':row.state,'external_rfq_id':row.external_rfq_id,'error_code':row.error_code,'tx_hash':row.tx_hash,'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat(),'legs':details, 'quote':{'id':str(row.quote_id),'expires_at':row.quote_expires_at.isoformat(),'price':str(row.quote_price),'total_required':str(row.quote_required),'net_receive':str(row.quote_receive) if row.quote_receive is not None else None} if row.quote_id and row.quote_expires_at else None}


def failure(request,exc):
    status=404 if exc.code=='not_found' else 409 if exc.code in ('mapping_invalid','idempotency_conflict','duplicate_unresolved','quote_expired','quote_unavailable','combo_quote_expired') else 422 if exc.code in ('invalid_amount','combo_legs_invalid') else 503
    return auth.error(request,status,exc.code.upper(),exc.message)


@router.post('/combo-rfqs',responses={201:{'description':'已记录询价'},202:{'description':'询价中或结果未知'}})
def create_combo(request: Request,body:ComboBody,idempotency_key:str=Header(...,alias='Idempotency-Key')):
    if not COMBO_EXECUTION_READY or not request.app.state.settings.combo_execution_enabled:return auth.error(request,503,'COMBO_DISABLED','组合交易尚未启用')
    try:
        try:key=uuid.UUID(idempotency_key)
        except ValueError:raise TradingError('invalid_amount','幂等键必须为 UUID')
        ident,created=request_quote(request.app.state.engine,request.app.state.settings,body,key)
        with Session(request.app.state.engine) as db:
            row=db.get(ComboRFQ,ident);status=202 if row.state in ('requesting','unknown') else 201 if created else 200
            return JSONResponse(status_code=status,content={'data':combo_data(db,row),'request_id':request.state.request_id})
    except TradingError as exc:return failure(request,exc)


@router.get('/combo-rfqs')
def list_combos(request:Request,limit:int=50,cursor:str | None=None):
    if not 1<=limit<=100:return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    query=select(ComboRFQ)
    try:
        if cursor:
            stamp,ident=cursor_decode(cursor);stamp=datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:raise ValueError()
            query=query.where(tuple_(ComboRFQ.created_at,ComboRFQ.id)<tuple_(stamp,uuid.UUID(ident)))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(ComboRFQ.created_at.desc(),ComboRFQ.id.desc()).limit(limit+1)));items=rows[:limit]
        next_cursor=cursor_encode([items[-1].created_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None
        return {'data':{'items':[combo_data(db,row) for row in items],'next_cursor':next_cursor},'request_id':request.state.request_id}


@router.get('/combo-rfqs/by-key/{key}')
def combo_by_key(request:Request,key:uuid.UUID,wallet_id:uuid.UUID):
    with Session(request.app.state.engine) as db:
        row=db.scalar(select(ComboRFQ).where(ComboRFQ.wallet_id==wallet_id,ComboRFQ.client_request_id==key))
        if not row:return auth.error(request,404,'NOT_FOUND','询价记录不存在，不能据此推断外部请求状态')
        return {'data':combo_data(db,row),'request_id':request.state.request_id}


@router.get('/combo-rfqs/{ident}')
def get_combo(request:Request,ident:uuid.UUID):
    with Session(request.app.state.engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:return auth.error(request,404,'NOT_FOUND','组合询价不存在')
        return {'data':combo_data(db,row),'request_id':request.state.request_id}


@router.post('/combo-rfqs/{ident}/accept',responses={202:{'description':'执行中或结果未知'}})
def accept_combo(request:Request,ident:uuid.UUID,body:AcceptBody):
    if not COMBO_EXECUTION_READY or not request.app.state.settings.combo_execution_enabled:return auth.error(request,503,'COMBO_DISABLED','组合交易尚未启用')
    try:
        accept_quote(request.app.state.engine,request.app.state.settings,ident,body.quote_id)
        with Session(request.app.state.engine) as db:
            row=db.get(ComboRFQ,ident);status=202 if row.state in ('accepting','executing','unknown') else 200
            return JSONResponse(status_code=status,content={'data':combo_data(db,row),'request_id':request.state.request_id})
    except TradingError as exc:return failure(request,exc)


@router.get('/combo-rfqs/{ident}/events')
def combo_events(request:Request,ident:uuid.UUID,limit:int=50,cursor:str | None=None):
    if not 1<=limit<=100:return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    query=select(ComboEvent).where(ComboEvent.rfq_id==ident)
    try:
        if cursor:
            stamp,last_id=cursor_decode(cursor);stamp=datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:raise ValueError()
            query=query.where(tuple_(ComboEvent.created_at,ComboEvent.id)>tuple_(stamp,uuid.UUID(last_id)))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        if not db.get(ComboRFQ,ident):return auth.error(request,404,'NOT_FOUND','组合询价不存在')
        rows=list(db.scalars(query.order_by(ComboEvent.created_at,ComboEvent.id).limit(limit+1)));items=rows[:limit]
        next_cursor=cursor_encode([items[-1].created_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None
        return {'data':{'items':[{'state':x.state,'code':x.code,'created_at':x.created_at.isoformat()} for x in items],'next_cursor':next_cursor},'request_id':request.state.request_id}
