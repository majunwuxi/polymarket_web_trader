"""组合询价状态机；所有外部发送均先保存不可重放状态。"""
from __future__ import annotations
import hashlib, hmac, json, threading, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from polymarket import RequestRejectedError
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .combo_catalog import fetch_positions
from .combo_gateway import ComboGateway
from .combo_quote import verify_quote, combo_position_id
from .combo_transport import accept_once
from .markets import valid_link
from .models import ComboRFQ, ComboLeg, ComboEvent, Game, Job, MarketLink, MarketOutcome, PolyMarket
from .scheduler import db_now, watch_game
from .trading import wallet_for, canonical
from .trading_gateway import TradingError, finite_decimal

# 报价对象只保存在单个 FastAPI 进程，绝不持久化或返回签名敏感字段。
_quotes = {}
_quote_lock = threading.Lock()


def expire_cached_quote(ident,quote_id):
    with _quote_lock:
        current=_quotes.get(ident)
        if current and current[0]==quote_id:_quotes.pop(ident,None)


def credential_fingerprint(settings,wallet):
    credential=settings.wallet_credentials.get(wallet.credential_ref)
    if credential is None:raise TradingError('credentials_missing','钱包凭证不存在')
    fields=(credential.private_key,credential.clob_api_key,credential.clob_api_secret,credential.clob_api_passphrase,credential.builder_api_key,credential.builder_api_secret,credential.builder_api_passphrase)
    raw=json.dumps([v.get_secret_value() if v else None for v in fields],separators=(',',':')).encode()
    return hmac.new(settings.auth_secret.get_secret_value().encode(),raw,hashlib.sha256).digest()


def body_data(body):
    amount=finite_decimal(body.quantity,'预算或份额')
    if amount.as_tuple().exponent < -6: raise TradingError('invalid_amount','金额或份额最多六位小数')
    legs=sorted((str(x.market_link_id),str(x.outcome_id)) for x in body.legs)
    if not 2<=len(legs)<=50 or len({x[1] for x in legs})!=len(legs): raise TradingError('combo_legs_invalid','需要 2 至 50 条不重复的组合腿')
    data={'wallet_id':str(body.wallet_id),'direction':body.direction,'quantity':canonical(amount),'legs':legs}
    parent=getattr(body,'parent_combo_id',None)
    if parent:
        if body.direction!='SELL':raise TradingError('combo_legs_invalid','组合平仓必须是卖出')
        data['parent_combo_id']=str(parent)
    return data,amount,hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def verify_combo_close_origin(db,parent_id,wallet_id,legs,amount,positions=None,exclude_id=None):
    if not parent_id:return None
    original=db.get(ComboRFQ,uuid.UUID(str(parent_id)))
    if not original or original.state!='confirmed' or original.direction!='BUY' or original.wallet_id!=wallet_id:
        raise TradingError('combo_close_invalid','原组合成交或钱包无法核实')
    old=list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==original.id)))
    if sorted((str(x.market_link_id),str(x.outcome_id)) for x in old)!=sorted(legs):
        raise TradingError('combo_close_invalid','组合平仓选项与原组合不一致')
    active_query=select(ComboRFQ.id).where(ComboRFQ.parent_combo_id==original.id,ComboRFQ.state.in_(['requesting','quoted','accepting','executing','unknown']))
    if exclude_id is not None:active_query=active_query.where(ComboRFQ.id!=exclude_id)
    if db.scalar(active_query.limit(1)):raise TradingError('duplicate_unresolved','原组合已有未解决的平仓请求')
    sold=db.scalar(select(func.coalesce(func.sum(ComboRFQ.quantity),0)).where(ComboRFQ.parent_combo_id==original.id,ComboRFQ.state=='confirmed'))
    with localcontext() as context:
        context.prec=90
        remaining=original.quote_receive-sold if original.quote_receive is not None else Decimal(0)
    if original.quote_receive is None or original.quote_receive<=0 or amount>remaining:
        raise TradingError('combo_close_invalid','平仓份额超过原组合可核验的报价份额')
    if positions is not None and any(positions.get(x.outcome_id)!=x.position_id for x in old):
        raise TradingError('combo_close_invalid','组合市场资产已变化')
    return original


def verify_combo_close_quote(quote,amount,expected_position):
    if Decimal(quote.total_required)!=amount:
        raise TradingError('combo_quote_mismatch','组合平仓报价未覆盖全部指定份额')
    if expected_position is not None and combo_position_id(quote)!=expected_position:
        raise TradingError('combo_quote_mismatch','组合平仓持仓标识与原组合不一致')


def resolve_legs(db, legs):
    result=[]
    for link_id,outcome_id in legs:
        link=db.get(MarketLink,uuid.UUID(link_id)); outcome=db.get(MarketOutcome,uuid.UUID(outcome_id))
        if not link or not outcome or link.market_id!=outcome.market_id or not valid_link(db,link): raise TradingError('mapping_invalid','组合腿赛事映射未确认或已过期')
        market=db.get(PolyMarket,link.market_id)
        result.append((link,market,outcome))
    if len({x[1].id for x in result}) != len(result): raise TradingError('combo_legs_invalid','同一市场不能重复作为组合腿')
    return result


def record(db,rfq,state,code=None):
    rfq.state=state;rfq.error_code=code;rfq.updated_at=db_now(db)
    db.add(ComboEvent(id=uuid.uuid4(),rfq_id=rfq.id,state=state,code=code))


def get_existing(db,wallet_id,key,fp):
    row=db.scalar(select(ComboRFQ).where(ComboRFQ.wallet_id==wallet_id,ComboRFQ.client_request_id==key))
    if row and row.fingerprint!=fp: raise TradingError('idempotency_conflict','幂等键对应其他组合请求')
    return row


def request_quote(engine,settings,body,key,gateway_cls=ComboGateway,catalog=fetch_positions):
    data,amount,fp=body_data(body);wallet=wallet_for(settings,body.wallet_id)
    identity=credential_fingerprint(settings,wallet)
    with Session(engine) as db:
        existing=get_existing(db,body.wallet_id,key,fp)
        if existing:return existing.id,False
        legs=resolve_legs(db,data['legs']);positions=catalog([x[1] for x in legs],[x[2] for x in legs])
        origin=verify_combo_close_origin(db,data.get('parent_combo_id'),body.wallet_id,data['legs'],amount,positions)
        if origin and origin.wallet_address!=wallet.wallet_address:raise TradingError('wallet_mismatch','原组合钱包已变化')
        expected_position=origin.combo_position_id if origin else None
        legacy_positions=None
        if origin and expected_position is None:
            from .close_positions import legacy_combo_receipt_positions
            legacy_positions=legacy_combo_receipt_positions(settings,wallet.wallet_address,origin.tx_hash,amount)
        with gateway_cls(settings,wallet) as gateway:
            try:
                with Session(engine) as tx,tx.begin():
                    previous=get_existing(tx,body.wallet_id,key,fp)
                    if previous:return previous.id,False
                    if getattr(body,'parent_combo_id',None):
                        tx.scalar(select(ComboRFQ).where(ComboRFQ.id==body.parent_combo_id).with_for_update())
                    for game_id in sorted({x[0].game_id for x in legs},key=str):tx.scalar(select(Game).where(Game.id==game_id).with_for_update())
                    for link_id,_ in data['legs']:tx.scalar(select(MarketLink).where(MarketLink.id==uuid.UUID(link_id)).with_for_update())
                    current=resolve_legs(tx,data['legs']); now=db_now(tx)
                    verify_combo_close_origin(tx,data.get('parent_combo_id'),body.wallet_id,data['legs'],amount,positions)
                    row=ComboRFQ(id=uuid.uuid4(),wallet_id=body.wallet_id,wallet_address=wallet.wallet_address,client_request_id=key,fingerprint=fp,direction=body.direction,quantity=amount,state='requesting',gateway_mode='requester',parent_combo_id=getattr(body,'parent_combo_id',None))
                    tx.add(row);tx.flush()
                    for link,market,outcome in current:tx.add(ComboLeg(id=uuid.uuid4(),rfq_id=row.id,game_id=link.game_id,market_link_id=link.id,outcome_id=outcome.id,position_id=positions[outcome.id]))
                    tx.add(ComboEvent(id=uuid.uuid4(),rfq_id=row.id,state='requesting'))
                    tx.add(Job(id=uuid.uuid4(),kind='combo_reconcile',dedupe_key='combo:'+str(row.id),combo_rfq_id=row.id,status='pending',due_at=now+timedelta(seconds=90)))
                    ident=row.id
            except IntegrityError as exc:
                with Session(engine) as tx:
                    previous=get_existing(tx,body.wallet_id,key,fp)
                    if previous:return previous.id,False
                    active=tx.scalar(select(ComboRFQ).where(ComboRFQ.wallet_id==body.wallet_id,ComboRFQ.fingerprint==fp,ComboRFQ.state.in_(['requesting','quoted','accepting','executing','unknown'])))
                    if active:raise TradingError('duplicate_unresolved','已有相同参数的未解决组合请求：'+str(active.id)) from exc
                raise
            try:
                result=gateway.client.request_combo_quote(leg_position_ids=[positions[x[2].id] for x in legs],direction=body.direction,**({'amount':amount} if body.direction=='BUY' else {'size':amount}))
                external=str(result.rfq_id)
                if not external or len(external)>255:raise ValueError('invalid rfq')
                quote=result.quote
                if quote is not None:
                    if data.get('parent_combo_id'):verify_combo_close_quote(quote,amount,expected_position)
                    if legacy_positions is not None and combo_position_id(quote) not in legacy_positions:
                        raise TradingError('combo_close_invalid','卖出报价资产与原交易回执不一致')
                    try:verify_quote(quote,rfq_id=external,direction=body.direction,quantity=amount,now_ms=int(datetime.now(timezone.utc).timestamp()*1000))
                    except TradingError as exc:
                        if exc.code!='combo_quote_expired':raise
                        with Session(engine) as tx,tx.begin():
                            job=tx.scalar(select(Job).where(Job.combo_rfq_id==ident).with_for_update())
                            row=tx.scalar(select(ComboRFQ).where(ComboRFQ.id==ident).with_for_update())
                            if row.state=='requesting':
                                row.external_rfq_id=external;record(tx,row,'expired','quote_expired')
                                if job.status=='pending':job.status='done'
                        return ident,True
                with Session(engine) as tx,tx.begin():
                    job=tx.scalar(select(Job).where(Job.combo_rfq_id==ident).with_for_update())
                    row=tx.scalar(select(ComboRFQ).where(ComboRFQ.id==ident).with_for_update())
                    if row.state!='requesting': return ident,True
                    row.external_rfq_id=external
                    if quote is None:
                        record(tx,row,'failed','no_quote')
                        if job.status=='pending':job.status='done'
                    else:
                        row.combo_position_id=combo_position_id(quote)
                        if row.parent_combo_id and legacy_positions is not None:
                            parent=tx.scalar(select(ComboRFQ).where(ComboRFQ.id==row.parent_combo_id).with_for_update())
                            if parent.combo_position_id not in (None,row.combo_position_id):raise TradingError('combo_close_invalid','原组合持仓标识已变更')
                            parent.combo_position_id=row.combo_position_id
                        row.quote_id=uuid.uuid4();row.quote_expires_at=datetime.fromtimestamp(quote.expires_at/1000,timezone.utc)
                        row.quote_price=quote.blended_price;row.quote_required=quote.total_required;row.quote_receive=quote.taker_amount if body.direction=='BUY' else quote.net_receive
                        record(tx,row,'quoted')
                        if job.status=='pending':job.due_at=row.quote_expires_at
                        with _quote_lock:
                            now_ms=int(datetime.now(timezone.utc).timestamp()*1000)
                            for old_key,old in list(_quotes.items()):
                                if old[1].expires_at<=now_ms:_quotes.pop(old_key,None)
                            _quotes[ident]=(row.quote_id,quote,identity)
                        delay=max(0,(quote.expires_at-int(datetime.now(timezone.utc).timestamp()*1000))/1000)
                        timer=threading.Timer(delay,expire_cached_quote,args=(ident,row.quote_id));timer.daemon=True;timer.start()
            except Exception as exc:
                rejected=isinstance(exc,RequestRejectedError) and exc.status in (400,401,403,429)
                with Session(engine) as tx,tx.begin():
                    job=tx.scalar(select(Job).where(Job.combo_rfq_id==ident).with_for_update())
                    row=tx.scalar(select(ComboRFQ).where(ComboRFQ.id==ident).with_for_update())
                    if row.state=='requesting':
                        record(tx,row,'rejected' if rejected else 'unknown','rfq_rejected' if rejected else 'request_unknown')
                        if job.status=='pending':
                            if rejected:job.status='done'
                            else:job.due_at=db_now(tx)+timedelta(minutes=2)
            return ident,True


def accept_quote(engine,settings,ident,quote_id,gateway_cls=ComboGateway,catalog=fetch_positions):
    with Session(engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:raise TradingError('not_found','组合询价不存在')
        if row.state!='quoted':return
        wallet=wallet_for(settings,row.wallet_id)
        if wallet.wallet_address!=row.wallet_address:raise TradingError('wallet_mismatch','钱包已变化')
        with _quote_lock: cached=_quotes.get(ident)
        if not cached or cached[0]!=quote_id:raise TradingError('quote_unavailable','报价已失效或服务重启，请重新询价')
        if not hmac.compare_digest(cached[2],credential_fingerprint(settings,wallet)):raise TradingError('wallet_mismatch','询价后钱包凭证已改变')
        quote=cached[1]
        verify_quote(quote,rfq_id=row.external_rfq_id,direction=row.direction,quantity=row.quantity,now_ms=int(datetime.now(timezone.utc).timestamp()*1000))
        saved=list(db.scalars(select(ComboLeg).where(ComboLeg.rfq_id==ident)))
        current=resolve_legs(db,[(str(x.market_link_id),str(x.outcome_id)) for x in saved])
        positions=catalog([x[1] for x in current],[x[2] for x in current])
        if any(positions[x.outcome_id]!=x.position_id for x in saved):raise TradingError('combo_market_mismatch','组合市场已变更')
        if row.parent_combo_id:
            origin=verify_combo_close_origin(db,row.parent_combo_id,row.wallet_id,[(str(x.market_link_id),str(x.outcome_id)) for x in saved],row.quantity,positions,exclude_id=row.id)
            if origin.combo_position_id is not None and row.combo_position_id!=origin.combo_position_id:raise TradingError('combo_quote_mismatch','组合平仓资产不一致')
    with gateway_cls(settings,wallet) as gateway:
        gateway.ensure_funding(quote, row.direction)
        with Session(engine) as tx,tx.begin():
            job=tx.scalar(select(Job).where(Job.combo_rfq_id==ident).with_for_update())
            row=tx.scalar(select(ComboRFQ).where(ComboRFQ.id==ident).with_for_update())
            if row.state!='quoted':return
            if row.quote_id!=quote_id:raise TradingError('quote_unavailable','报价不匹配')
            now=db_now(tx)
            verify_quote(quote,rfq_id=row.external_rfq_id,direction=row.direction,quantity=row.quantity,now_ms=int(now.timestamp()*1000))
            legs=list(tx.scalars(select(ComboLeg).where(ComboLeg.rfq_id==ident)))
            for game_id in sorted({x.game_id for x in legs},key=str):tx.scalar(select(Game).where(Game.id==game_id).with_for_update())
            for leg in sorted(legs,key=lambda x:str(x.market_link_id)):tx.scalar(select(MarketLink).where(MarketLink.id==leg.market_link_id).with_for_update())
            resolve_legs(tx,[(str(x.market_link_id),str(x.outcome_id)) for x in legs])
            for game_id in sorted({x.game_id for x in legs},key=str):
                game=tx.get(Game,game_id)
                if game and game.provider!='polymarket':watch_game(tx,game_id)
            external_id=row.external_rfq_id
            row.accept_started_at=now;record(tx,row,'accepting')
            if job.status=='pending':job.due_at=now+timedelta(seconds=90)
        with _quote_lock:_quotes.pop(ident,None)
        try:
            result=accept_once(gateway.client,quote)
            state='executing' if result.status=='executing' and str(result.rfq_id)==external_id else 'rejected' if result.status=='failed' and str(result.rfq_id)==external_id else 'unknown'
            code=None if state=='executing' else 'accept_failed' if state=='rejected' else 'accept_unknown'
        except Exception:state='unknown';code='accept_unknown'
        with Session(engine) as tx,tx.begin():
            job=tx.scalar(select(Job).where(Job.combo_rfq_id==ident).with_for_update())
            row=tx.scalar(select(ComboRFQ).where(ComboRFQ.id==ident).with_for_update())
            if row.state=='accepting':
                record(tx,row,state,code)
                if job.status=='pending':
                    if state=='rejected':job.status='done'
                    else:job.due_at=db_now(tx)+timedelta(seconds=30)
