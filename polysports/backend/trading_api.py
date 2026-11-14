from __future__ import annotations
import uuid
from datetime import datetime,timedelta
from typing import Literal
from fastapi import APIRouter,Header,Query,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,StrictStr
from sqlalchemy import select,tuple_
from sqlalchemy.orm import Session
from . import auth
from .models import Job,MarketLink,TradeIntent,TradeStateEvent,TradeSubmission
from .scheduler import db_now
from .sports_api import cursor_decode,cursor_encode
from .trading import as_data,execute,prepare,wallet_for
from .trading_gateway import Gateway,TradingError,restriction

router=APIRouter(prefix='/api/v1')

class OrderBody(BaseModel):
    wallet_id:uuid.UUID
    market_link_id:uuid.UUID
    outcome_id:uuid.UUID
    side:Literal['BUY','SELL']
    limit_price:StrictStr=Field(min_length=1,max_length=64)
    size:StrictStr=Field(min_length=1,max_length=64)

class SubmitBody(OrderBody):
    preview_token:StrictStr=Field(min_length=1,max_length=3000)


def ok(request,data):return {'data':data,'request_id':request.state.request_id}

def error(request,exc):
    code=exc.code
    status=403 if code=='geoblock_blocked' else 409 if code in ('preview_stale','mapping_invalid','market_unavailable','idempotency_conflict','duplicate_unresolved') else 422 if code in ('invalid_amount','price_tick_invalid','size_invalid') else 503
    return auth.error(request,status,code.upper(),exc.message,delay=exc.retry_after)


@router.post('/single-orders/preview')
def preview(request:Request,body:OrderBody):
    try: return ok(request,prepare(request.app.state.engine,request.app.state.settings,body))
    except TradingError as exc:return error(request,exc)


@router.post('/single-orders',responses={201:{'description':'已记录'},202:{'description':'处理中或结果未知'}})
def submit(request:Request,body:SubmitBody,idempotency_key:str=Header(...,alias='Idempotency-Key')):
    try:
        try: key=uuid.UUID(idempotency_key)
        except (ValueError,TypeError):raise TradingError('invalid_amount','幂等键必须是 UUID')
        ident,created=execute(request.app.state.engine,request.app.state.settings,body,key)
        with Session(request.app.state.engine) as db:
            intent=db.get(TradeIntent,ident);data=as_data(intent,db)
            submission=db.scalar(select(TradeSubmission).where(TradeSubmission.intent_id==ident))
            submission_data={'state':submission.state,'error_code':submission.error_code,'started_at':submission.started_at.isoformat(),'finished_at':submission.finished_at.isoformat() if submission.finished_at else None} if submission else None
            status=202 if intent.state in ('submitting','unknown') else (201 if created else 200)
            return JSONResponse(status_code=status,content=ok(request,{'intent':data,'submission':submission_data}))
    except TradingError as exc:return error(request,exc)


@router.get('/trade-intents/by-key/{request_id}')
def by_key(request:Request,request_id:uuid.UUID,wallet_id:uuid.UUID):
    with Session(request.app.state.engine) as db:
        intent=db.scalar(select(TradeIntent).where(TradeIntent.wallet_id==wallet_id,TradeIntent.client_request_id==request_id))
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        return ok(request,as_data(intent,db))


@router.get('/trade-intents')
def list_intents(request:Request,limit:int=50,wallet_id:uuid.UUID | None=None,game_id:uuid.UUID | None=None,state:str | None=None,kind:Literal['single'] | None=None,cursor:str | None=None,start:datetime | None=Query(None,alias='from'),end:datetime | None=Query(None,alias='to')):
    if (start and start.tzinfo is None) or (end and end.tzinfo is None) or (start and end and end<=start):return auth.error(request,422,'VALIDATION_ERROR','时间范围无效')
    if limit<1 or limit>100:return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    query=select(TradeIntent)
    if wallet_id:query=query.where(TradeIntent.wallet_id==wallet_id)
    if game_id:query=query.where(TradeIntent.game_id==game_id)
    if state:query=query.where(TradeIntent.state==state)
    if start:query=query.where(TradeIntent.created_at>=start)
    if end:query=query.where(TradeIntent.created_at<end)
    try:
        if cursor:
            date,ident=cursor_decode(cursor);date=datetime.fromisoformat(date)
            if date.tzinfo is None:raise ValueError()
            query=query.where(tuple_(TradeIntent.created_at,TradeIntent.id)<tuple_(date,uuid.UUID(ident)))
    except (ValueError,TypeError):return auth.error(request,422,'VALIDATION_ERROR','分页参数无效')
    with Session(request.app.state.engine) as db:
        rows=list(db.scalars(query.order_by(TradeIntent.created_at.desc(),TradeIntent.id.desc()).limit(limit+1)));items=rows[:limit]
        return ok(request,{'items':[as_data(x,db) for x in items],'next_cursor':cursor_encode([items[-1].created_at.isoformat(),str(items[-1].id)]) if len(rows)>limit else None})


@router.get('/trade-intents/{intent_id}')
def detail(request:Request,intent_id:uuid.UUID):
    with Session(request.app.state.engine) as db:
        intent=db.get(TradeIntent,intent_id)
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        subs=db.scalars(select(TradeSubmission).where(TradeSubmission.intent_id==intent_id))
        events=db.scalars(select(TradeStateEvent).where(TradeStateEvent.intent_id==intent_id).order_by(TradeStateEvent.created_at))
        return ok(request,{'intent':as_data(intent,db),'rfqs':[],'submissions':[{'state':x.state,'error_code':x.error_code,'started_at':x.started_at.isoformat(),'finished_at':x.finished_at.isoformat() if x.finished_at else None} for x in subs],'state_events':[{'state':x.state,'code':x.code,'created_at':x.created_at.isoformat()} for x in events]})


@router.get('/trade-intents/{intent_id}/resolution')
def resolution(request:Request,intent_id:uuid.UUID):
    import httpx
    from .models import Game, MarketOutcome, PolyMarket, ResultBindingRequest, Team
    from .order_resolution import compare_moneyline_score, read_resolution
    from .sports_sources import SourceError
    with Session(request.app.state.engine) as db:
        intent=db.get(TradeIntent,intent_id)
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        game=db.get(Game,intent.game_id);link=db.get(MarketLink,intent.market_link_id)
        market=db.get(PolyMarket,link.market_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        binding=db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id==game.id))
        verified=bool(binding and binding.state=='bound' and game.status=='finished' and game.home_score is not None and game.away_score is not None)
        base={'score_verified':verified,'home_score':game.home_score if verified else None,'away_score':game.away_score if verified else None,
              'matched_size':str(intent.matched_size) if intent.matched_size is not None else None,'side':intent.side}
        if intent.matched_size is None or intent.matched_size<=0 or not verified:
            return ok(request,{**base,'state':'pending'})
        condition,index=market.condition_id,outcome.outcome_index
        home=db.get(Team,game.home_team_id);away=db.get(Team,game.away_team_id)
        score_context={'sport':game.sport,'question':market.question,'outcome_label':outcome.label,
            'home':home.source_name,'away':away.source_name,'home_score':game.home_score,
            'away_score':game.away_score,'provider_status':game.provider_status}
    try:
        with httpx.Client() as client: data=read_resolution(client,condition,index)
        if data['state']=='resolved':
            data['score_consistency']=compare_moneyline_score(**score_context,outcome_won=data['outcome_won'])
        return ok(request,{**base,**data})
    except SourceError:
        return auth.error(request,503,'RESOLUTION_UNAVAILABLE','市场结算状态暂不可用')


def update_cancel(engine,intent_id,state,code=None,delay=None):
    with Session(engine) as db,db.begin():
        job=db.scalar(select(Job).where(Job.trade_intent_id==intent_id).with_for_update())
        intent=db.scalar(select(TradeIntent).where(TradeIntent.id==intent_id).with_for_update())
        if not intent or intent.cancel_state not in ('submitting','unknown'):return
        now=db_now(db);intent.cancel_state=state;intent.updated_at=now
        if state=='confirmed':intent.state='cancelled'
        intent.error_code=code;intent.retry_after_seconds=delay
        db.add(TradeStateEvent(id=uuid.uuid4(),intent_id=intent.id,state=intent.state,code='cancel_'+state))
        if job and job.status in ('pending','done'):
            job.status='pending';job.due_at=now+timedelta(seconds=30)


@router.post('/single-orders/{intent_id}/cancel',responses={202:{'description':'撤单结果未知'}})
def cancel(request:Request,intent_id:uuid.UUID):
    engine=request.app.state.engine
    with Session(engine) as db,db.begin():
        job=db.scalar(select(Job).where(Job.trade_intent_id==intent_id).with_for_update())
        intent=db.scalar(select(TradeIntent).where(TradeIntent.id==intent_id).with_for_update())
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        if intent.cancel_state and intent.cancel_state!='rejected':
            return JSONResponse(status_code=202 if intent.cancel_state in ('submitting','unknown') else 200,content=ok(request,{'intent':as_data(intent,db)}))
        if not intent.external_order_id or intent.state not in ('pending','partially_filled','unknown'):
            return auth.error(request,409,'CANCEL_UNAVAILABLE','订单状态不允许撤单')
        order_id=intent.external_order_id;wallet_ident=intent.wallet_id;wallet_address=intent.wallet_address
        intent.cancel_state='submitting';intent.updated_at=db_now(db)
    sent=False
    try:
        wallet=wallet_for(request.app.state.settings,wallet_ident)
        if wallet.wallet_address!=wallet_address:raise TradingError('wallet_mismatch','订单钱包已变化')
        with Gateway(request.app.state.settings,wallet) as gateway:
            # 初始化失败可证明尚未调用撤单；调用后的异常保持未知。
            try:
                sent=True
                result=gateway.cancel(order_id)
                if order_id in result.canceled:update_cancel(engine,intent_id,'confirmed')
                elif order_id in result.not_canceled:update_cancel(engine,intent_id,'rejected','cancel_rejected')
                else:update_cancel(engine,intent_id,'unknown','cancel_unknown')
            except Exception as exc:
                issue=restriction(exc)
                update_cancel(engine,intent_id,'rejected' if issue else 'unknown',issue.code if issue else 'cancel_unknown',issue.retry_after if issue else None)
    except Exception as exc:
        issue=exc if isinstance(exc,TradingError) else restriction(exc)
        update_cancel(engine,intent_id,'unknown' if sent else 'rejected','cancel_unknown' if sent else (issue.code if issue else 'cancel_not_sent'),issue.retry_after if issue else None)
    with Session(engine) as db:
        intent=db.get(TradeIntent,intent_id)
        return JSONResponse(status_code=202 if intent.cancel_state in ('submitting','unknown') else 200,content=ok(request,{'intent':as_data(intent,db)}))


@router.post('/trade-intents/{intent_id}/reconcile',status_code=202)
def schedule_reconcile(request:Request,intent_id:uuid.UUID):
    with Session(request.app.state.engine) as db,db.begin():
        job=db.scalar(select(Job).where(Job.trade_intent_id==intent_id).with_for_update())
        if not job:return auth.error(request,404,'NOT_FOUND','订单对账任务不存在')
        if job.status in ('pending','done','failed'):
            job.status='pending';job.due_at=db_now(db)
        return ok(request,{'intent_id':str(intent_id),'job_id':str(job.id),'status':job.status})


@router.get('/order-context/{link_id}/{outcome_id}')
def order_context(request:Request,link_id:uuid.UUID,outcome_id:uuid.UUID):
    from .models import Game,MarketOutcome,PolyMarket,Team
    from .markets import valid_link
    with Session(request.app.state.engine) as db:
        link=db.get(MarketLink,link_id);outcome=db.get(MarketOutcome,outcome_id)
        if not link or not outcome or link.market_id!=outcome.market_id or not valid_link(db,link):
            return auth.error(request,409,'MAPPING_INVALID','赛事映射未确认或已过期')
        market=db.get(PolyMarket,link.market_id);game=db.get(Game,link.game_id)
        home=db.get(Team,game.home_team_id);away=db.get(Team,game.away_team_id)
        return ok(request,{'market_question':market.question,'outcome_label':outcome.label,'home':home.name_zh or home.source_name,'away':away.name_zh or away.source_name,'starts_at':game.starts_at.isoformat(),'condition_id':market.condition_id,'asset_id':outcome.asset_id,'protocol_version':market.protocol_version})


@router.get('/outcomes/{outcome_id}/book')
def public_book(request:Request,outcome_id:uuid.UUID):
    import httpx
    from decimal import Decimal,InvalidOperation
    from .models import MarketOutcome,PolyMarket
    with Session(request.app.state.engine) as db:
        outcome=db.get(MarketOutcome,outcome_id)
        if not outcome:return auth.error(request,404,'NOT_FOUND','市场选项不存在')
        market=db.get(PolyMarket,outcome.market_id)
        try:
            response=httpx.get('https://clob.polymarket.com/book',params={'token_id':outcome.asset_id},timeout=10,follow_redirects=False)
            if response.status_code==425:
                from .sports_sources import retry_delay
                delay=retry_delay(response) if response.headers.get('Retry-After') else None
                return auth.error(request,503,'ENGINE_RESTARTING','撮合引擎正在重启',delay=delay)
            response.raise_for_status();body=response.json()
            if not isinstance(body,dict) or str(body.get('asset_id'))!=outcome.asset_id or str(body.get('market','')).lower()!=market.condition_id:raise ValueError('book mismatch')
            if isinstance(body['tick_size'],float) or isinstance(body['min_order_size'],float):raise ValueError('imprecise rules')
            tick=Decimal(str(body['tick_size']));minimum=Decimal(str(body['min_order_size']))
            if not tick.is_finite() or not minimum.is_finite() or not 0<tick<Decimal('0.5') or minimum<=0:raise ValueError('rules invalid')
            def levels(name):
                rows=body.get(name)
                if not isinstance(rows,list):raise ValueError('invalid levels')
                result=[]
                for item in rows:
                    if not isinstance(item,dict):raise ValueError('invalid level')
                    if isinstance(item['price'],float) or isinstance(item['size'],float):raise ValueError('imprecise level')
                    p=Decimal(str(item['price']));s=Decimal(str(item['size']))
                    if not p.is_finite() or not s.is_finite() or not 0<p<1 or s<0:raise ValueError('invalid level')
                    result.append({'price':str(p),'size':str(s)})
                return result
            return ok(request,{'outcome_id':str(outcome_id),'bids':levels('bids'),'asks':levels('asks'),'tick_size':str(tick),'min_order_size':str(minimum),'observed_at':db_now(db).isoformat()})
        except Exception:return auth.error(request,503,'BOOK_UNAVAILABLE','订单簿暂不可用')


class AttachOrder(BaseModel):
    order_id:StrictStr=Field(pattern=r'^0x[0-9a-fA-F]{64}$')
    acknowledge_uncertainty:Literal[True]


@router.get('/trade-intents/{intent_id}/reconcile-candidates')
def reconcile_candidates(request:Request,intent_id:uuid.UUID):
    from .models import MarketOutcome,PolyMarket
    from .trade_reconcile import order_matches
    with Session(request.app.state.engine) as db:
        intent=db.get(TradeIntent,intent_id)
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        if intent.state!='unknown' or intent.external_order_id:return auth.error(request,409,'RECONCILE_UNAVAILABLE','订单不需要候选查询')
        link=db.get(MarketLink,intent.market_link_id);market=db.get(PolyMarket,link.market_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        try:
            wallet=wallet_for(request.app.state.settings,intent.wallet_id)
            if wallet.wallet_address!=intent.wallet_address:raise TradingError('wallet_mismatch','钱包信息已变化')
            with Gateway(request.app.state.settings,wallet) as gateway: orders,truncated=gateway.candidate_orders(outcome.asset_id)
            candidates=[]
            for order in orders:
                valid,_=order_matches(intent,market,outcome,order,order_id=str(order.id),require_time=True)
                if valid:candidates.append({'order_id':str(order.id),'created_at':order.created_at.isoformat(),'status':str(order.status)})
            return ok(request,{'items':candidates,'truncated':truncated,'scope':'open_orders_only','notice':'候选仅表示参数相似，不证明是本次订单；请到 Polymarket 独立核实。'})
        except Exception:
            return auth.error(request,503,'RECONCILE_UNAVAILABLE','无法查询候选订单')


@router.post('/trade-intents/{intent_id}/attach-order',status_code=202)
def attach_order(request:Request,intent_id:uuid.UUID,body:AttachOrder):
    from .models import AuditEvent,MarketOutcome,PolyMarket
    from .trade_reconcile import order_matches
    from sqlalchemy.exc import IntegrityError
    engine=request.app.state.engine;order_id=body.order_id.lower()
    with Session(engine) as db:
        intent=db.get(TradeIntent,intent_id)
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        if intent.state!='unknown' or intent.external_order_id:return auth.error(request,409,'RECONCILE_UNAVAILABLE','订单状态不允许绑定')
        link=db.get(MarketLink,intent.market_link_id);market=db.get(PolyMarket,link.market_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        try:
            wallet=wallet_for(request.app.state.settings,intent.wallet_id)
            if wallet.wallet_address!=intent.wallet_address:raise ValueError('wallet mismatch')
            with Gateway(request.app.state.settings,wallet) as gateway: order=gateway.get_order(order_id)
            valid,_=order_matches(intent,market,outcome,order,order_id=order_id,require_time=True)
            if not valid:return auth.error(request,409,'ORDER_EVIDENCE_MISMATCH','订单信息不匹配，不能关联')
        except Exception:return auth.error(request,503,'RECONCILE_UNAVAILABLE','无法核对交易所订单')
    try:
        with Session(engine) as db,db.begin():
            job=db.scalar(select(Job).where(Job.trade_intent_id==intent_id).with_for_update())
            intent=db.scalar(select(TradeIntent).where(TradeIntent.id==intent_id).with_for_update())
            if intent.state!='unknown' or intent.external_order_id:return auth.error(request,409,'RECONCILE_UNAVAILABLE','订单状态已变化')
            intent.external_order_id=order_id;intent.updated_at=db_now(db)
            db.add(TradeStateEvent(id=uuid.uuid4(),intent_id=intent_id,state='unknown',code='external_order_attached'))
            db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='trade_order_attached',entity_type='trade_intent',entity_id=intent_id,details={}))
            if job and job.status in ('pending','done','failed'):job.status='pending';job.due_at=db_now(db)
        return ok(request,{'intent_id':str(intent_id),'status':'unknown','order_id':order_id})
    except IntegrityError:return auth.error(request,409,'ORDER_ALREADY_LINKED','该订单已关联其他意图')


@router.get('/trade-intents/{intent_id}/fills')
def fills(request:Request,intent_id:uuid.UUID):
    from .models import MarketOutcome,PolyMarket,TradeFill
    from .trading import canonical
    from sqlalchemy import text
    engine=request.app.state.engine
    with Session(engine) as db:
        intent=db.get(TradeIntent,intent_id)
        if not intent:return auth.error(request,404,'NOT_FOUND','订单不存在')
        if not intent.external_order_id:return auth.error(request,409,'ORDER_UNKNOWN','订单编号未知，无法查询成交')
        link=db.get(MarketLink,intent.market_link_id);market=db.get(PolyMarket,link.market_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        try:
            wallet=wallet_for(request.app.state.settings,intent.wallet_id)
            if wallet.wallet_address!=intent.wallet_address:raise TradingError('wallet_mismatch','订单钱包已变化')
            with Gateway(request.app.state.settings,wallet) as gateway:
                records,truncated=gateway.fills(outcome.asset_id,market.condition_id,intent.external_order_id,intent.side,intent.wallet_address,intent.requested_size)
        except Exception:
            return auth.error(request,503,'FILLS_UNAVAILABLE','成交记录暂不可用')
    with Session(engine) as db,db.begin():
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'trade-fills:'+str(intent_id)})
        now=db_now(db)
        known={x.external_trade_id:x for x in db.scalars(select(TradeFill).where(TradeFill.intent_id==intent_id).with_for_update())}
        for item in records:
            existing=known.get(item['trade_id'])
            if existing and (existing.size!=item['size'] or existing.price!=item['price'] or existing.matched_at!=item['matched_at']):
                return auth.error(request,409,'FILL_CONFLICT','成交记录不一致，请核实来源')
        for item in records:
            existing=known.get(item['trade_id'])
            if existing: existing.status=item['status'];existing.observed_at=now
            else: db.add(TradeFill(id=uuid.uuid4(),intent_id=intent_id,external_trade_id=item['trade_id'],size=item['size'],price=item['price'],status=item['status'],matched_at=item['matched_at'],observed_at=now))
        db.flush()
        return ok(request,{'items':[{'trade_id':x['trade_id'],'size':canonical(x['size']),'price':canonical(x['price']),'status':x['status'],'matched_at':x['matched_at'].isoformat()} for x in records],'truncated':truncated,'notice':'成交状态由交易所提供，未确认状态不得视为结算完成。'})
