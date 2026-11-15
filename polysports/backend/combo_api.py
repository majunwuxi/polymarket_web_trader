"""组合腿只读检查，以及组合询价与接受入口（由组合执行开关控制）。"""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from . import auth
from .close_policy import MARKET_CLOSED_REASON, RESULT_CONFIRMED_REASON
from .combo_catalog import fetch_positions
from .combo_resolution import summarize_combo
from .order_resolution import judge_score_detail
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
from .models import ComboRFQ, ComboLeg, ComboEvent, Game, Team, WatchedGame, ResultBindingRequest, Job


class ComboLegBody(BaseModel):
    market_link_id: uuid.UUID
    outcome_id: uuid.UUID


class ComboBody(BaseModel):
    wallet_id: uuid.UUID
    direction: Literal['BUY','SELL']
    quantity: StrictStr = Field(min_length=1,max_length=64)
    legs: list[ComboLegBody] = Field(min_length=2,max_length=50)
    parent_combo_id: uuid.UUID | None = None


class AcceptBody(BaseModel):
    quote_id: uuid.UUID


def combo_data(db,row):
    """组合记录自带逐腿赛果判断（纯本地只读）；各腿市场终态另由 /resolution 查询。"""
    legs=list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==row.id)))
    details=[]
    for leg in legs:
        game=db.get(Game,leg.game_id); link=db.get(MarketLink,leg.market_link_id); market=db.get(PolyMarket,link.market_id); outcome=db.get(MarketOutcome,leg.outcome_id); home=db.get(Team,game.home_team_id); away=db.get(Team,game.away_team_id)
        watch=db.scalar(select(WatchedGame).where(WatchedGame.game_id==game.id))
        binding=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game.id))
        job=db.scalar(select(Job).where(Job.watched_game_id==watch.id,Job.kind=='result_poll')) if watch else None
        verified=bool(binding and binding.state=='bound' and game.status=='finished'
                      and game.home_score is not None and game.away_score is not None)
        # 与单场及 /resolution 完全共用同一套严格规则，不从比分猜测无法识别的命题。
        context={'sport':game.sport,'question':market.question,'outcome_label':outcome.label,
                 'home':getattr(home,'source_name',None),'away':getattr(away,'source_name',None),
                 'home_score':game.home_score if verified else None,'away_score':game.away_score if verified else None,
                 'provider_status':game.provider_status}
        won,basis=judge_score_detail(**context) if verified else (None,'外部赛果尚未绑定核实或比分未确认')
        details.append({'game_id':str(leg.game_id),'sport':game.sport,'market_link_id':str(leg.market_link_id),'outcome_id':str(leg.outcome_id),'position_id':leg.position_id,'home':home.name_zh or home.source_name,'away':away.name_zh or away.source_name,'starts_at':game.starts_at.isoformat(),'market_question':market.question,'outcome_label':outcome.label,'watch_state':watch.state if watch else None,'binding_state':binding.state if binding else None,'home_score':game.home_score,'away_score':game.away_score,'next_poll_at':job.due_at.isoformat() if job and job.status=='pending' else None,
                        'game_finished':game.status=='finished','score_verified':verified,
                        'market_closed':market.accepting_orders is False,
                        'result_won':won,'result_basis':basis})
    score_summary=summarize_combo([{'result_won':x['result_won'],'market_won':None} for x in details],direction=row.direction)
    # 串关任一腿赛果已确认即胜负已定；任一腿停单也说明组合已进入结算，均不允许再平仓。
    close_reason=(RESULT_CONFIRMED_REASON if any(x['score_verified'] for x in details)
                  else MARKET_CLOSED_REASON if any(x['market_closed'] for x in details) else None)
    return {'id':str(row.id),'wallet_id':str(row.wallet_id),'wallet_address':row.wallet_address,'direction':row.direction,'quantity':str(row.quantity),'state':row.state,'external_rfq_id':row.external_rfq_id,'error_code':row.error_code,'tx_hash':row.tx_hash,'parent_combo_id':str(row.parent_combo_id) if row.parent_combo_id else None,'combo_position_id':row.combo_position_id,'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat(),'legs':details,
            'by_score':score_summary['by_score'],'judged_legs':score_summary['judged_legs'],
            'leg_count':score_summary['leg_count'],'score_reason':score_summary['reason'],
            'close_blocked':close_reason is not None,'close_block_reason':close_reason,
            'quote':{'id':str(row.quote_id),'expires_at':row.quote_expires_at.isoformat(),'price':str(row.quote_price),'total_required':str(row.quote_required),'net_receive':str(row.quote_receive) if row.quote_receive is not None else None} if row.quote_id and row.quote_expires_at else None}


def failure(request,exc):
    status=404 if exc.code=='not_found' else 409 if exc.code in ('mapping_invalid','idempotency_conflict','duplicate_unresolved','quote_expired','quote_unavailable','combo_quote_expired','position_settled') else 422 if exc.code in ('invalid_amount','combo_legs_invalid') else 403 if exc.code=='geoblock_blocked' else 503
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
def list_combos(request:Request,limit:int=50,cursor:str | None=None,state:Literal['confirmed']|None=None,game_id:uuid.UUID|None=None):
    if not 1<=limit<=100:return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    query=select(ComboRFQ)
    if state:query=query.where(ComboRFQ.state==state)
    if game_id:query=query.where(select(ComboLeg.id).where(ComboLeg.rfq_id==ComboRFQ.id,ComboLeg.game_id==game_id).exists())
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


@router.get('/combo-rfqs/{ident}/close-context')
def combo_close_context(request:Request,ident:uuid.UUID):
    from .close_positions import inspect_combo_position
    try:return {'data':inspect_combo_position(request.app.state.engine,request.app.state.settings,ident),'request_id':request.state.request_id}
    except TradingError as exc:return failure(request,exc)


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


# 单次核对最多串行查询 20 条腿的市场终态，避免一条组合拖住整个只读请求。
MAX_RESOLUTION_LEGS = 20


@router.get('/combo-rfqs/{ident}/resolution')
def combo_resolution(request:Request,ident:uuid.UUID):
    """逐腿并列返回赛果判断与各腿底层市场的 Polymarket 终态结算，按串关全中口径汇总。"""
    import httpx
    from .combo_resolution import leg_agreement, summarize_combo
    from .order_resolution import judge_score_detail, read_resolution
    from .sports_sources import SourceError
    with Session(request.app.state.engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:return auth.error(request,404,'NOT_FOUND','组合询价不存在')
        meta={'id':str(row.id),'direction':row.direction,'state':row.state}
        legs=list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==ident).order_by(ComboLeg.id)))
        items=[]
        for number,leg in enumerate(legs,1):
            game=db.get(Game,leg.game_id);link=db.get(MarketLink,leg.market_link_id)
            market=db.get(PolyMarket,link.market_id) if link else None
            outcome=db.get(MarketOutcome,leg.outcome_id)
            home=db.get(Team,game.home_team_id);away=db.get(Team,game.away_team_id)
            binding=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game.id))
            verified=bool(binding and binding.state=='bound' and game.status=='finished'
                          and game.home_score is not None and game.away_score is not None)
            context={'sport':game.sport,'question':market.question if market else None,
                     'outcome_label':outcome.label if outcome else None,
                     'home':getattr(home,'source_name',None),'away':getattr(away,'source_name',None),
                     'home_score':game.home_score if verified else None,'away_score':game.away_score if verified else None,
                     'provider_status':game.provider_status}
            # 与单场共用同一套严格规则：大小分/让分/胜负可判断，其余不猜。
            won,basis=judge_score_detail(**context) if verified else (None,'外部赛果尚未绑定核实或比分未确认')
            items.append({'leg_no':number,'game_id':str(game.id),'sport':game.sport,
                'home':home.name_zh or home.source_name,'away':away.name_zh or away.source_name,
                'starts_at':game.starts_at.isoformat(),'market_question':context['question'],
                'outcome_label':context['outcome_label'],'home_score':context['home_score'],
                'away_score':context['away_score'],'game_finished':game.status=='finished',
                'game_status':game.status,'score_verified':verified,
                'binding_state':binding.state if binding else None,
                'result_judgment':{'state':'judged' if won is not None else ('unsupported' if verified else 'unavailable'),
                                   'outcome_won':won,'basis':basis},
                'condition_id':market.condition_id if market else None,
                'outcome_index':outcome.outcome_index if outcome else None})
    with httpx.Client() as client:
        for position,item in enumerate(items):
            condition,index=item['condition_id'],item['outcome_index']
            detail={'state':'pending','outcome_won':None,'market_status':None,
                    'market_status_text':None,'resolved_at':None,'market_error':None}
            if position>=MAX_RESOLUTION_LEGS:
                detail.update(state='unavailable',market_error='超过单次核对腿数上限，未查询该腿市场终态')
            elif not condition or index not in (0,1):
                detail.update(state='unavailable',market_error='腿的市场条件或选项编号缺失')
            else:
                try:
                    detail=read_resolution(client,condition,index)
                    detail.setdefault('market_error',None)
                    for key in ('outcome_won','market_status','market_status_text','resolved_at'):
                        detail.setdefault(key,None)
                except SourceError as exc:
                    detail={'state':'unavailable','outcome_won':None,'market_status':None,
                            'market_status_text':None,'resolved_at':None,'market_error':str(exc)}
            item['market']=detail
            item['result_won']=item['result_judgment']['outcome_won']
            item['market_won']=detail['outcome_won'] if detail['state']=='resolved' else None
            item['leg_agreement']=leg_agreement(item)
    summary=summarize_combo(items,direction=meta['direction'])
    if len(items)>MAX_RESOLUTION_LEGS:
        summary['notice']+=f' 本次仅核对前 {MAX_RESOLUTION_LEGS} 条腿，其余腿未查询市场终态。'
    return {'data':{**meta,'legs':items,**summary},'request_id':request.state.request_id}
