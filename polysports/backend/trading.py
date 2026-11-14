from __future__ import annotations
import base64,hashlib,hmac,json,re,uuid
from datetime import timedelta
from decimal import Decimal, localcontext
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .config import Settings
from .market_api import wallet_id
from .markets import valid_link
from .models import Game,Job,MarketLink,MarketOutcome,PolyEvent,PolyMarket,Team,TradeIntent,TradeStateEvent,TradeSubmission
from .scheduler import db_now,watch_game
from .trading_gateway import Gateway,TradingError,finite_decimal,restriction

PREVIEW_SECONDS=30


def canonical(number: Decimal) -> str:
    text=format(number,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def exact_notional(price,size):
    with localcontext() as ctx:
        ctx.prec=80
        return price*size


def wallet_for(settings:Settings,ident:uuid.UUID):
    value=next((w for w in settings.wallets if wallet_id(w.wallet_address)==ident),None)
    if value is None: raise TradingError('wallet_not_found','未找到已配置的钱包')
    return value


def normalize(body):
    price=finite_decimal(body.limit_price,'价格');size=finite_decimal(body.size,'份额')
    data={'wallet_id':str(body.wallet_id),'market_link_id':str(body.market_link_id),'outcome_id':str(body.outcome_id),'side':body.side,'limit_price':canonical(price),'size':canonical(size)}
    return data,price,size


def fingerprint(data): return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def encode_token(settings,payload):
    value=base64.urlsafe_b64encode(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).decode().rstrip('=')
    signature=hmac.new(settings.auth_secret.get_secret_value().encode(),('single-preview:'+value).encode(),hashlib.sha256).hexdigest()
    return value+'.'+signature


def decode_token(settings,token):
    try:
        encoded,signature=token.split('.',1)
        expected=hmac.new(settings.auth_secret.get_secret_value().encode(),('single-preview:'+encoded).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected): raise ValueError()
        value=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        if not isinstance(value,dict): raise ValueError()
        return value
    except Exception as exc: raise TradingError('preview_stale','预览无效或已过期，请重新预览') from exc


def resolve(db,body):
    link=db.get(MarketLink,body.market_link_id)
    outcome=db.get(MarketOutcome,body.outcome_id)
    if not link or not outcome or link.market_id!=outcome.market_id or not valid_link(db,link): raise TradingError('mapping_invalid','赛事映射未确认或已过期')
    market=db.get(PolyMarket,link.market_id)
    if market.accepting_orders is not True or market.protocol_version not in ('v1','v2') or outcome.asset_kind!=('position' if market.protocol_version=='v2' else 'ctf_token'):
        raise TradingError('market_unavailable','市场不可接单或协议未核实')
    return link,market,outcome


def event_for_inspection(db,link,market):
    event=db.get(PolyEvent,market.poly_event_id)
    if event is not None:
        event._expected_source_game_id=link.evidence.get('source_game_id')
    return event


def prepare(engine,settings,body,gateway_cls=Gateway):
    data,price,size=normalize(body);wallet=wallet_for(settings,body.wallet_id)
    with Session(engine) as db:
        link,market,outcome=resolve(db,body)
        game_id=link.game_id
        with gateway_cls(settings,wallet) as gateway: context=gateway.inspect(market,outcome,body.side,price,size,event_for_inspection(db,link,market))
        if not context.get('hash'): raise TradingError('book_unavailable','订单簿缺少有效版本标识')
        now=db_now(db)
    token=encode_token(settings,{'fingerprint':fingerprint(data),'book_hash':context['hash'],'expires_at':int((now+timedelta(seconds=PREVIEW_SECONDS)).timestamp())})
    return {'preview_token':token,'expires_at':(now+timedelta(seconds=PREVIEW_SECONDS)).isoformat(),'game_id':str(game_id),**data,'estimated_notional':canonical(exact_notional(price,size)),'fee_estimate':None,'book_observed_at':now.isoformat(),'tick_size':context['tick'],'min_order_size':context['minimum'],'balance':context['balance'],'allowance_status':context['allowance_status'],'warnings':['真实资金订单；接受不代表成交']}


def get_existing(db,wallet,request_id,fp):
    intent=db.scalar(select(TradeIntent).where(TradeIntent.wallet_id==wallet,TradeIntent.client_request_id==request_id))
    if intent and intent.request_fingerprint!=fp: raise TradingError('idempotency_conflict','同一幂等键对应不同订单参数')
    return intent


def as_data(intent,db=None):
    value={'id':str(intent.id),'wallet_id':str(intent.wallet_id),'game_id':str(intent.game_id),'market_link_id':str(intent.market_link_id),'outcome_id':str(intent.outcome_id),'side':intent.side,'limit_price':canonical(intent.limit_price),'size':canonical(intent.requested_size),'state':intent.state,'external_order_id':intent.external_order_id,'matched_size':canonical(intent.matched_size) if intent.matched_size is not None else None,'cancel_state':intent.cancel_state,'error_code':intent.error_code,'retry_after_seconds':intent.retry_after_seconds,'created_at':intent.created_at.isoformat(),'updated_at':intent.updated_at.isoformat()}
    if db:
        game=db.get(Game,intent.game_id);link=db.get(MarketLink,intent.market_link_id);market=db.get(PolyMarket,link.market_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        home=db.get(Team,game.home_team_id);away=db.get(Team,game.away_team_id)
        value.update({'home':home.name_zh or home.source_name,'away':away.name_zh or away.source_name,'starts_at':game.starts_at.isoformat(),'market_question':market.question,'outcome_label':outcome.label})
    return value


def save_result(engine,intent_id,state,code=None,order_id=None,retry_after=None):
    with Session(engine) as db, db.begin():
        job=db.scalar(select(Job).where(Job.trade_intent_id==intent_id).with_for_update())
        intent=db.scalar(select(TradeIntent).where(TradeIntent.id==intent_id).with_for_update())
        if not intent or intent.state not in ('submitting','unknown'): return
        now=db_now(db); intent.state=state;intent.external_order_id=order_id;intent.error_code=code;intent.retry_after_seconds=retry_after;intent.updated_at=now
        submission=db.scalar(select(TradeSubmission).where(TradeSubmission.intent_id==intent_id).with_for_update())
        submission.state=state;submission.error_code=code;submission.finished_at=now
        db.add(TradeStateEvent(id=uuid.uuid4(),intent_id=intent.id,state=state,code=code))
        if state=='pending' and order_id:
            from .result_binding import request_binding_for_accepted_order
            request_binding_for_accepted_order(db,intent.game_id)
        if job and job.status=='pending':
            if state=='rejected': job.status='done'
            else: job.due_at=now+timedelta(seconds=30)


def execute(engine,settings,body,request_id:uuid.UUID,gateway_cls=Gateway):
    data,price,size=normalize(body);fp=fingerprint(data);wallet=wallet_for(settings,body.wallet_id)
    with Session(engine) as db:
        existing=get_existing(db,body.wallet_id,request_id,fp)
        if existing: return existing.id,False
    token=decode_token(settings,body.preview_token)
    with Session(engine) as db:
        now=db_now(db)
        if token.get('fingerprint')!=fp or type(token.get('expires_at')) is not int or token['expires_at']<=int(now.timestamp()): raise TradingError('preview_stale','预览已过期或订单参数改变')
        link,market,outcome=resolve(db,body)
        with gateway_cls(settings,wallet) as gateway:
            context=gateway.inspect(market,outcome,body.side,price,size,event_for_inspection(db,link,market))
            if not context.get('hash') or context['hash']!=token.get('book_hash'): raise TradingError('preview_stale','盘口变化，请重新预览')
            try:
                with Session(engine) as tx, tx.begin():
                    existing=get_existing(tx,body.wallet_id,request_id,fp)
                    if existing: return existing.id,False
                    tx.scalar(select(Game).where(Game.id==link.game_id).with_for_update())
                    tx.scalar(select(PolyMarket).where(PolyMarket.id==link.market_id).with_for_update())
                    tx.scalar(select(MarketLink).where(MarketLink.id==body.market_link_id).with_for_update())
                    verified,_,_=resolve(tx,body)
                    now=db_now(tx)
                    if token['expires_at']<=int(now.timestamp()): raise TradingError('preview_stale','预览已过期，请重新预览')
                    intent=TradeIntent(id=uuid.uuid4(),wallet_id=body.wallet_id,wallet_address=wallet.wallet_address,client_request_id=request_id,request_fingerprint=fp,game_id=verified.game_id,market_link_id=verified.id,outcome_id=body.outcome_id,side=body.side,limit_price=price,requested_size=size,state='submitting')
                    tx.add(intent);tx.flush()
                    game=tx.get(Game,intent.game_id)
                    if game and game.provider!='polymarket': watch_game(tx,intent.game_id)
                    tx.add(TradeSubmission(id=uuid.uuid4(),intent_id=intent.id,state='submitting'))
                    tx.add(TradeStateEvent(id=uuid.uuid4(),intent_id=intent.id,state='submitting'))
                    tx.add(Job(id=uuid.uuid4(),kind='trade_reconcile',dedupe_key='trade:'+str(intent.id),trade_intent_id=intent.id,due_at=now+timedelta(seconds=60),status='pending'))
                    intent_id=intent.id
            except IntegrityError as exc:
                with Session(engine) as lookup:
                    existing=get_existing(lookup,body.wallet_id,request_id,fp)
                    if existing: return existing.id,False
                    active=lookup.scalar(select(TradeIntent).where(TradeIntent.wallet_id==body.wallet_id,TradeIntent.request_fingerprint==fp,TradeIntent.state.in_(['submitting','pending','partially_filled','unknown'])))
                    if active: raise TradingError('duplicate_unresolved','已有相同参数的未解决订单：'+str(active.id)) from exc
                raise
            try: signed=gateway.sign(outcome,body.side,price,size)
            except TradingError as exc:
                save_result(engine,intent_id,'rejected',exc.code,retry_after=exc.retry_after);return intent_id,True
            except Exception:
                save_result(engine,intent_id,'rejected','sign_failed');return intent_id,True
            try:
                response=gateway.post(signed)
                if getattr(response,'ok',None) is True and re.fullmatch(r'0x[0-9a-fA-F]{64}',str(getattr(response,'order_id',''))): save_result(engine,intent_id,'pending',order_id=str(response.order_id).lower())
                elif getattr(response,'ok',None) is False: save_result(engine,intent_id,'rejected',code=str(getattr(response,'code','rejected'))[:80])
                else: save_result(engine,intent_id,'unknown','response_unknown')
            except Exception as exc:
                problem=restriction(exc)
                if problem: save_result(engine,intent_id,'rejected',problem.code,retry_after=problem.retry_after)
                else: save_result(engine,intent_id,'unknown','submission_unknown')
            return intent_id,True
