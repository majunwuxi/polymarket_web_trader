"""组合腿只读检查，以及组合一步下单入口（兼容原两步询价/接受，由组合执行开关控制）。"""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Request
from sqlalchemy.orm import Session

from . import auth
from .close_policy import MARKET_CLOSED_REASON, RESULT_CONFIRMED_REASON, score_verified_clause, settled_visibility_cutoff
from .combo_catalog import fetch_positions
from .combo_fills import buy_summary as combo_buy_summary, fill_payload
from .combo_resolution import summarize_combo
from .order_resolution import judge_score_detail
from .pnl import combo_pnl
from .markets import valid_link
from .models import MarketLink, MarketOutcome, PolyMarket
from .trading_gateway import TradingError

router = APIRouter(prefix='/api/v1')
# 组合资金操作仍须由部署环境显式启用。
COMBO_EXECUTION_READY = True


@router.post('/combo-rfqs/{combo_id}/reconcile', status_code=202)
def schedule_combo_reconcile(request: Request, combo_id: uuid.UUID):
    from sqlalchemy import select
    from .models import ComboRFQ, Job
    from .scheduler import db_now
    with Session(request.app.state.engine) as db, db.begin():
        if not db.get(ComboRFQ, combo_id):
            return auth.error(request, 404, 'NOT_FOUND', '组合询价不存在')
        job = db.scalar(select(Job).where(Job.combo_rfq_id == combo_id).with_for_update())
        if not job:
            return auth.error(request, 404, 'NOT_FOUND', '组合对账任务不存在')
        if job.status in ('pending', 'done', 'failed'):
            job.status = 'pending'
            job.due_at = db_now(db)
            job.failure_count = 0
            job.last_error_code = job.last_error_summary = None
        return {'data': {'combo_id': str(combo_id), 'job_id': str(job.id), 'status': job.status}, 'request_id': request.state.request_id}


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
from sqlalchemy import and_,or_,select,tuple_
from datetime import datetime
from .sports_api import cursor_decode, cursor_encode
from .combo_service import request_quote, accept_quote, discard_unaccepted_quote
from .models import ComboRFQ, ComboLeg, ComboEvent, ComboFill, Game, Team, WatchedGame, ResultBindingRequest, Job
from .prefetch import objects_by_id, rows_by_attr


class ComboLegBody(BaseModel):
    market_link_id: uuid.UUID
    outcome_id: uuid.UUID


class ComboBody(BaseModel):
    wallet_id: uuid.UUID
    direction: Literal['BUY','SELL']
    quantity: StrictStr = Field(min_length=1,max_length=64)
    legs: list[ComboLegBody] = Field(min_length=2,max_length=50)
    parent_combo_id: uuid.UUID | None = None
    # 一步下单：拿到当前官方组合报价后立即在同一请求内签名接受，不等待二次确认。
    auto_accept: bool = False


class AcceptBody(BaseModel):
    quote_id: uuid.UUID


_MISSING = object()


def combo_prefetch(db, rows):
    """一次性加载组合列表所需的关联对象，供 combo_data 复用。"""
    rows=list(rows)
    if not rows:
        return {'legs':{},'games':{},'links':{},'markets':{},'outcomes':{},'teams':{},
                'watches':{},'bindings':{},'jobs':{},'fills':{}}
    rfq_ids=[row.id for row in rows]
    legs_by_rfq=rows_by_attr(db,ComboLeg,'rfq_id',rfq_ids)
    legs=[leg for values in legs_by_rfq.values() for leg in values]
    game_ids={leg.game_id for leg in legs}
    link_ids={leg.market_link_id for leg in legs}
    outcome_ids={leg.outcome_id for leg in legs}
    games=objects_by_id(db,Game,game_ids)
    links=objects_by_id(db,MarketLink,link_ids)
    markets=objects_by_id(db,PolyMarket,{link.market_id for link in links.values()})
    outcomes=objects_by_id(db,MarketOutcome,outcome_ids)
    teams=objects_by_id(db,Team,
                        {game.home_team_id for game in games.values()} |
                        {game.away_team_id for game in games.values()})
    watches={watch.game_id:watch for watch in db.scalars(select(WatchedGame).where(WatchedGame.game_id.in_(game_ids)))} if game_ids else {}
    bindings={binding.game_id:binding for binding in db.scalars(select(ResultBindingRequest).where(ResultBindingRequest.game_id.in_(game_ids)))} if game_ids else {}
    watch_ids={watch.id for watch in watches.values()}
    jobs={job.watched_game_id:job for job in db.scalars(select(Job).where(Job.watched_game_id.in_(watch_ids),Job.kind=='result_poll'))} if watch_ids else {}
    fills={fill.rfq_id:fill for fill in db.scalars(select(ComboFill).where(ComboFill.rfq_id.in_(rfq_ids),ComboFill.state=='confirmed'))}
    return {'legs':legs_by_rfq,'games':games,'links':links,'markets':markets,'outcomes':outcomes,
            'teams':teams,'watches':watches,'bindings':bindings,'jobs':jobs,'fills':fills}


def combo_data(db,row,fill=_MISSING,prefetch=None):
    """组合记录自带逐腿赛果判断（纯本地只读）；各腿市场终态另由 /resolution 查询。

    ``fill`` 由列表接口批量传入以避免 N+1；``prefetch`` 由 combo_prefetch 生成，
    可把一页内所有腿的关联对象合并为固定数量的 IN 查询。
    """
    legs=prefetch['legs'].get(row.id,[]) if prefetch is not None else list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==row.id)))
    details=[]
    for leg in legs:
        if prefetch is not None:
            game=prefetch['games'].get(leg.game_id)
            link=prefetch['links'].get(leg.market_link_id)
            market=prefetch['markets'].get(link.market_id) if link else None
            outcome=prefetch['outcomes'].get(leg.outcome_id)
            home=prefetch['teams'].get(game.home_team_id) if game else None
            away=prefetch['teams'].get(game.away_team_id) if game else None
            watch=prefetch['watches'].get(game.id) if game else None
            binding=prefetch['bindings'].get(game.id) if game else None
            job=prefetch['jobs'].get(watch.id) if watch else None
        else:
            game=db.get(Game,leg.game_id); link=db.get(MarketLink,leg.market_link_id); market=db.get(PolyMarket,link.market_id); outcome=db.get(MarketOutcome,leg.outcome_id); home=db.get(Team,game.home_team_id); away=db.get(Team,game.away_team_id)
            watch=db.scalar(select(WatchedGame).where(WatchedGame.game_id==game.id))
            binding=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game.id))
            job=db.scalar(select(Job).where(Job.watched_game_id==watch.id,Job.kind=='result_poll')) if watch else None
        if not game or not market or not outcome or not home or not away:
            continue
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
    quote=({'id':str(row.quote_id),'expires_at':row.quote_expires_at.isoformat(),'price':str(row.quote_price),
            'total_required':str(row.quote_required),
            'net_receive':str(row.quote_receive) if row.quote_receive is not None else None}
           if row.quote_id and row.quote_expires_at else None)
    if fill is _MISSING:
        fill=prefetch['fills'].get(row.id) if prefetch is not None else db.scalar(select(ComboFill).where(ComboFill.rfq_id==row.id,ComboFill.state=='confirmed'))
    fill_data=fill_payload(fill)
    pnl=combo_pnl(direction=row.direction,by_score=score_summary['by_score'],
                  quantity=row.quantity,quote=quote,fill=fill_data)
    return {'id':str(row.id),'wallet_id':str(row.wallet_id),'wallet_address':row.wallet_address,'direction':row.direction,'quantity':str(row.quantity),'state':row.state,'external_rfq_id':row.external_rfq_id,'error_code':row.error_code,'error_text':combo_error_text(row.error_code),'tx_hash':row.tx_hash,'accept_order_hash':row.accept_order_hash,'parent_combo_id':str(row.parent_combo_id) if row.parent_combo_id else None,'combo_position_id':row.combo_position_id,'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat(),'legs':details,
            'by_score':score_summary['by_score'],'judged_legs':score_summary['judged_legs'],
            'leg_count':score_summary['leg_count'],'score_reason':score_summary['reason'],
            'close_blocked':close_reason is not None,'close_block_reason':close_reason,
            'quote':quote,'fill':fill_data,'pnl':pnl,
            'buy_summary':combo_buy_summary(row.direction,quote,fill_data)}


def combo_data_one(db,row):
    return combo_data(db,row,prefetch=combo_prefetch(db,[row]))


def failure(request,exc):
    status=404 if exc.code=='not_found' else 409 if exc.code in ('mapping_invalid','idempotency_conflict','duplicate_unresolved','quote_expired','quote_unavailable','combo_quote_expired','position_settled') else 422 if exc.code in ('invalid_amount','combo_legs_invalid') else 403 if exc.code=='geoblock_blocked' else 503
    return auth.error(request,status,exc.code.upper(),exc.message)


# 上游 Combo RFQ 拒绝/失败原因的中文说明。上游可能返回 SDK 尚未枚举的新码，
# 未命中时页面原样显示错误码，不把未知原因改写成笼统的「账户权限」提示。
COMBO_ERROR_TEXT = {
    'maker_declined': '做市商拒绝本次接受（Last Look），报价方未确认成交',
    'maker_already_responded': '做市商已响应过该报价请求',
    'maker_quote_limited': '做市商报价额度受限',
    'acceptance_window_expired': '上游接受窗口已过期，报价未能成交',
    'expired_rfq': '上游报价已过期，未能成交',
    'submission_window_closed': '上游提交窗口已关闭，未能成交',
    'execution_failed': '上游执行失败，未能成交',
    'contradictory_legs': '组合腿互相矛盾，上游拒绝',
    'leg_metadata_unavailable': '组合腿元数据不可用，上游无法核验',
    'balance_validation_failed': '上游校验余额不足，未能成交',
    'allowance_validation_failed': '上游校验授权不足，未能成交',
    'pre_execution_balance_reservation_failed': '上游预执行余额预留失败，未能成交',
    'trade_submission_failed': '上游交易提交失败，未能成交',
    'no_quotes': '上游暂无可用报价',
    'size_too_large': '下单金额超出上游报价上限',
    'invalid_quote': '上游判定报价无效',
    'quote_mismatch': '上游判定报价与请求不匹配',
    'invalid_rfq': '上游判定报价请求无效',
    'unknown_rfq': '上游找不到该报价请求',
    'invalid_rfq_state': '上游报价状态已不允许接受',
    'invalid_acceptance': '上游判定接受请求无效',
    'invalid_confirmation': '上游确认信息无效',
    'invalid_execution_result': '上游执行结果无效',
    'invalid_signature': '上游判定签名无效',
    'invalid_signature_type': '上游判定签名类型无效',
    'invalid_identity': '上游身份校验未通过',
    'address_mismatch': '钱包地址与上游记录不一致',
    'invalid_order_side': '上游判定买卖方向无效',
    'order_side_or_token_does_not_match_request': '订单方向或资产与请求不匹配',
    'invalid_role': '账户角色不符合上游要求',
    'unauthorized_role': '账户角色未获授权',
    'unauthenticated': '上游认证失败，请检查 CLOB 凭证',
    'invalid_json': '上游判定请求格式无效',
    'invalid_message': '上游判定签名消息无效',
    'service_unavailable': '上游服务暂不可用',
    'internal_error': '上游内部错误',
    'request_failed': '上游请求失败',
    'rfq_failed': '上游报价请求失败，未能成交',
    'rfq_expired': '上游报价已过期，未能成交',
    'rfq_canceled': '上游取消了该报价请求',
    'rfq_rejected': '上游拒绝本次询价',
    'accept_failed': '上游拒绝本次接受，未能成交',
    'accept_unknown': '接受结果未知，请勿重复提交',
    'request_unknown': '询价结果未知，请勿重复提交',
    'no_quote': '本次没有收到报价，未成交',
    'quote_expired': '报价已过期，未成交',
    'reconcile_stale': '组合对账已停止自动重试，请到 Polymarket 核对后重新安排对账',
    'reconcile_failed': '组合对账连续失败，已停止自动重试，请到 Polymarket 核对持仓和成交记录',
    'rfq_not_found': '上游已找不到该组合 RFQ，请到 Polymarket 核对持仓和成交记录',
}


def combo_error_text(code):
    return COMBO_ERROR_TEXT.get(str(code).lower()) if code else None


def auto_accept_current_quote(request,ident,created):
    """一步下单的核心编排：官方 Combo 只能经 RFQ 网关成交，报价即当前市场价。

    request_quote 保存状态并取得报价后，立刻在同一请求内执行原本需要用户
    二次点击的 accept_quote，避免报价在等待确认期间过期。有效期、余额、
    授权、钱包指纹、腿身份与单次发送保护全部沿用原校验，没有绕过。
    幂等重放（created=False）绝不再签名下单。
    """
    if not created:return
    with Session(request.app.state.engine) as db:
        row=db.get(ComboRFQ,ident)
        quote_id=row.quote_id if row is not None and row.state=='quoted' else None
    if quote_id is None:return
    try:
        accept_quote(request.app.state.engine,request.app.state.settings,ident,quote_id)
    except TradingError as exc:
        discard_unaccepted_quote(request.app.state.engine,ident,exc.code)
        raise


@router.post('/combo-rfqs',responses={201:{'description':'已记录组合请求'},202:{'description':'已提交执行中或结果未知'}})
def create_combo(request: Request,body:ComboBody,idempotency_key:str=Header(...,alias='Idempotency-Key')):
    if not COMBO_EXECUTION_READY or not request.app.state.settings.combo_execution_enabled:return auth.error(request,503,'COMBO_DISABLED','组合交易尚未启用')
    try:
        try:key=uuid.UUID(idempotency_key)
        except ValueError:raise TradingError('invalid_amount','幂等键必须为 UUID')
        ident,created=request_quote(request.app.state.engine,request.app.state.settings,body,key)
        if body.auto_accept:auto_accept_current_quote(request,ident,created)
        with Session(request.app.state.engine) as db:
            row=db.get(ComboRFQ,ident);status=202 if row.state in ('requesting','unknown','accepting','executing') else 201 if created else 200
            return JSONResponse(status_code=status,content={'data':combo_data_one(db,row),'request_id':request.state.request_id})
    except TradingError as exc:return failure(request,exc)


def hide_old_settled_combos(query,cutoff=None):
    """交易记录视图：在分页前排除「最后一场比赛开赛超过 24 小时且已结算」的组合。

    「最后一场」是各腿开赛时间的最大值：只有不存在任何一条腿在阈值之后开赛，
    才说明整组比赛都已开赛超过 24 小时。已结算沿用组合列表 close_blocked 口径：
    任一腿赛果已绑定核实或该腿市场已停单，串关胜负即已定，不允许再平仓。
    过滤写成相关子查询，不改动外层的 (created_at,id) 游标与 LIMIT 分页。
    """
    cutoff=cutoff or settled_visibility_cutoff()
    any_recent=select(ComboLeg.id).join(Game,Game.id==ComboLeg.game_id).where(
        ComboLeg.rfq_id==ComboRFQ.id,Game.starts_at>=cutoff).exists()
    any_settled=select(ComboLeg.id).join(Game,Game.id==ComboLeg.game_id).join(
        MarketLink,MarketLink.id==ComboLeg.market_link_id).join(
        PolyMarket,PolyMarket.id==MarketLink.market_id).where(
        ComboLeg.rfq_id==ComboRFQ.id,
        or_(score_verified_clause(Game),PolyMarket.accepting_orders.is_(False))).exists()
    return query.where(~and_(~any_recent,any_settled))


@router.get('/combo-rfqs')
def list_combos(request:Request,limit:int=50,cursor:str | None=None,state:Literal['confirmed']|None=None,game_id:uuid.UUID|None=None,hide_settled:bool=False):
    if not 1<=limit<=100:return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    query=select(ComboRFQ)
    if state:query=query.where(ComboRFQ.state==state)
    if game_id:query=query.where(select(ComboLeg.id).where(ComboLeg.rfq_id==ComboRFQ.id,ComboLeg.game_id==game_id).exists())
    # 交易记录页专用；默认不带参数时保持原列表语义，其它调用方不受影响。
    if hide_settled:query=hide_old_settled_combos(query)
    try:
        if cursor:
            stamp,ident=cursor_decode(cursor);stamp=datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:raise ValueError()
            query=query.where(tuple_(ComboRFQ.created_at,ComboRFQ.id)<tuple_(stamp,uuid.UUID(ident)))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(ComboRFQ.created_at.desc(),ComboRFQ.id.desc()).limit(limit+1)));items=rows[:limit]
        prefetch=combo_prefetch(db,items)
        next_cursor=cursor_encode([items[-1].created_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None
        return {'data':{'items':[combo_data(db,row,prefetch=prefetch) for row in items],'next_cursor':next_cursor},'request_id':request.state.request_id}


@router.get('/combo-rfqs/by-key/{key}')
def combo_by_key(request:Request,key:uuid.UUID,wallet_id:uuid.UUID):
    with Session(request.app.state.engine) as db:
        row=db.scalar(select(ComboRFQ).where(ComboRFQ.wallet_id==wallet_id,ComboRFQ.client_request_id==key))
        if not row:return auth.error(request,404,'NOT_FOUND','询价记录不存在，不能据此推断外部请求状态')
        return {'data':combo_data_one(db,row),'request_id':request.state.request_id}


@router.get('/combo-rfqs/{ident}')
def get_combo(request:Request,ident:uuid.UUID):
    with Session(request.app.state.engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:return auth.error(request,404,'NOT_FOUND','组合询价不存在')
        return {'data':combo_data_one(db,row),'request_id':request.state.request_id}


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
            return JSONResponse(status_code=status,content={'data':combo_data_one(db,row),'request_id':request.state.request_id})
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
