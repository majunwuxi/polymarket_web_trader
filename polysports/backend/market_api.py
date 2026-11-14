from __future__ import annotations
import uuid
from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import auth
from .markets import begin_refresh, refresh_game, trading_blockers, valid_link
from .models import AuditEvent, ComboLeg, Game, MarketLink, MarketOutcome, MarketRefresh, PolyEvent, PolyMarket, TradeIntent
from .scheduler import db_now

router=APIRouter(prefix='/api/v1')


def ok(request,data): return {'data':data,'request_id':request.state.request_id}


def link_data(db,link):
    market=db.get(PolyMarket,link.market_id); event=db.get(PolyEvent,market.poly_event_id)
    valid=valid_link(db,link,require_confirmed=False)
    state='stale' if link.state=='confirmed' and not valid else link.state
    outcomes=db.scalars(select(MarketOutcome).where(MarketOutcome.market_id==market.id).order_by(MarketOutcome.outcome_index))
    return {'link':{'id':str(link.id),'state':state,'evidence':link.evidence,'confirmed_at':link.confirmed_at.isoformat() if link.confirmed_at else None,'can_confirm':link.state in ('candidate','stale') and valid,'trade_ready':False,'blockers':trading_blockers(db,link)},'market':{'id':str(market.id),'gamma_market_id':market.gamma_market_id,'condition_id':market.condition_id,'protocol_version':market.protocol_version,'question':market.question,'slug':market.slug,'accepting_orders':market.accepting_orders,'combo_enabled':market.combo_enabled,'last_verified_at':market.last_verified_at.isoformat(),'outcomes':[{'id':str(x.id),'label':x.label,'outcome_index':x.outcome_index,'asset_id':x.asset_id,'asset_kind':x.asset_kind,'combo_leg_id':x.combo_leg_id} for x in outcomes]},'event':{'id':event.gamma_event_id,'title':event.title,'starts_at':event.starts_at.isoformat(),'home':event.home_name,'away':event.away_name}}


@router.get('/games/{game_id}/markets')
def list_markets(request:Request,game_id:uuid.UUID):
    with Session(request.app.state.engine) as db:
        if not db.get(Game,game_id): return auth.error(request,404,'NOT_FOUND','赛事不存在')
        refresh=db.get(MarketRefresh,game_id)
        links=db.scalars(select(MarketLink).where(MarketLink.game_id==game_id).order_by(MarketLink.created_at,MarketLink.id))
        return ok(request,{'items':[link_data(db,x) for x in links],'refresh':{'status':refresh.status,'started_at':refresh.started_at.isoformat(),'finished_at':refresh.finished_at.isoformat() if refresh.finished_at else None,'error_code':refresh.error_code} if refresh else None})


@router.post('/games/{game_id}/markets/refresh',status_code=202)
def refresh(request:Request,game_id:uuid.UUID,background:BackgroundTasks):
    state=begin_refresh(request.app.state.engine,game_id)
    if state=='not_found': return auth.error(request,404,'NOT_FOUND','赛事不存在')
    if state=='started': background.add_task(refresh_game,request.app.state.engine,game_id)
    return ok(request,{'game_id':str(game_id),'status':'refreshing'})


@router.post('/market-links/{link_id}/confirm')
def confirm(request:Request,link_id:uuid.UUID):
    try:
        with Session(request.app.state.engine) as db, db.begin():
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('polysports-gamma'))"))
            initial=db.get(MarketLink,link_id)
            if not initial: return auth.error(request,404,'NOT_FOUND','映射不存在')
            db.scalar(select(Game).where(Game.id==initial.game_id).with_for_update())
            db.scalar(select(PolyMarket).where(PolyMarket.id==initial.market_id).with_for_update())
            link=db.scalar(select(MarketLink).where(MarketLink.id==link_id).with_for_update())
            if link.state=='rejected' or not valid_link(db,link,require_confirmed=False):
                db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='market_link_conflict',entity_type='market_link',entity_id=link.id,details={'reason':'invalid_or_stale'}))
                return auth.error(request,409,'MAPPING_INVALID','映射证据不足或已过期，请重新刷新核对')
            if link.state=='confirmed': return ok(request,link_data(db,link))
            referenced=db.scalar(select(TradeIntent.id).join(MarketLink,TradeIntent.market_link_id==MarketLink.id).where(MarketLink.market_id==link.market_id,MarketLink.id!=link.id).limit(1))
            referenced_combo=db.scalar(select(ComboLeg.id).join(MarketLink,ComboLeg.market_link_id==MarketLink.id).where(MarketLink.market_id==link.market_id,MarketLink.id!=link.id).limit(1))
            if referenced or referenced_combo:
                db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='market_link_conflict',entity_type='market_link',entity_id=link.id,details={'reason':'market_has_trade_history'}))
                return auth.error(request,409,'MAPPING_CONFLICT','该市场已有其他赛事的订单历史')
            for other in db.scalars(select(MarketLink).where(MarketLink.market_id==link.market_id,MarketLink.state=='confirmed',MarketLink.id!=link.id).with_for_update()):
                if valid_link(db,other):
                    db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='market_link_conflict',entity_type='market_link',entity_id=link.id,details={'reason':'already_confirmed'}))
                    return auth.error(request,409,'MAPPING_CONFLICT','该市场已关联其他赛事')
                other.state='stale';other.updated_at=db_now(db)
            db.flush()
            now=db_now(db);link.state='confirmed';link.confirmed_by=request.state.admin_session['admin_id'];link.confirmed_at=now;link.updated_at=now
            db.add(AuditEvent(id=uuid.uuid4(),actor_id=link.confirmed_by,action='market_link_confirmed',entity_type='market_link',entity_id=link.id,details={}))
            db.flush()
            return ok(request,link_data(db,link))
    except IntegrityError:
        with Session(request.app.state.engine) as db, db.begin():
            db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='market_link_conflict',entity_type='market_link',entity_id=link_id,details={'reason':'concurrent_confirmation'}))
        return auth.error(request,409,'MAPPING_CONFLICT','该市场已关联其他赛事')


@router.post('/market-links/{link_id}/reject')
def reject(request:Request,link_id:uuid.UUID):
    with Session(request.app.state.engine) as db, db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('polysports-gamma'))"))
        link=db.scalar(select(MarketLink).where(MarketLink.id==link_id).with_for_update())
        if not link: return auth.error(request,404,'NOT_FOUND','映射不存在')
        if db.scalar(select(TradeIntent.id).where(TradeIntent.market_link_id==link.id).limit(1)) or db.scalar(select(ComboLeg.id).where(ComboLeg.market_link_id==link.id).limit(1)):
            return auth.error(request,409,'MAPPING_IN_USE','映射已被订单引用，不能拒绝')
        if link.state!='rejected':
            link.state='rejected';link.confirmed_at=None;link.confirmed_by=None;link.updated_at=db_now(db)
            db.add(AuditEvent(id=uuid.uuid4(),actor_id=request.state.admin_session['admin_id'],action='market_link_rejected',entity_type='market_link',entity_id=link.id,details={}))
        return ok(request,link_data(db,link))


def wallet_id(address): return uuid.uuid5(uuid.NAMESPACE_URL,'polysports:wallet:'+address)


WALLET_DIAGNOSTICS = {
    'credentials_missing': '未找到钱包凭据，请检查 credential_ref 与凭据名称是否一致。',
    'geoblock_blocked': 'Polymarket 报告服务器当前出口受地区限制。',
    'geoblock_unknown': '无法确认地区状态，请检查服务器到 Polymarket 的网络连接。',
    'wallet_invalid': '签名私钥格式无效，请在服务器安全配置中检查。',
    'signer_mismatch': '签名私钥与配置的签名地址不一致。',
    'wallet_mismatch': '钱包地址、签名地址或钱包类型不匹配，请核对账户配置。',
    'wallet_deployment_unknown': '无法通过 Polygon RPC 核验钱包，请检查 RPC 配置与网络连接。',
    'rpc_wrong_chain': 'RPC 连接的不是 Polygon 主网，请检查网络配置。',
    'wallet_undeployed': '未在 Polygon 主网发现钱包合约，请核对钱包地址和类型。',
    'sdk_unavailable': '交易客户端初始化失败，请检查钱包类型、CLOB 凭据和网络。',
    'balance_unavailable': '余额查询失败，请检查 CLOB 凭据和网络后重试。',
    'engine_restarting': 'Polymarket 撮合服务正在重启，请稍后重试。',
    'post_only_mode': 'Polymarket 当前处于交易限制状态，请稍后重试。',
    'status_unavailable': '钱包状态暂时不可用，请检查服务配置和网络后重试。',
}


@router.get('/wallets')
def wallets(request:Request):
    return ok(request,{'items':[{'id':str(wallet_id(w.wallet_address)),'label':w.label,'wallet_address':w.wallet_address,'signer_address':w.signer_address,'wallet_type':w.wallet_type} for w in request.app.state.settings.wallets]})


@router.get('/wallets/{wallet_id_value}/status')
def wallet_status(request:Request,wallet_id_value:uuid.UUID):
    wallet=next((w for w in request.app.state.settings.wallets if wallet_id(w.wallet_address)==wallet_id_value),None)
    if not wallet: return auth.error(request,404,'NOT_FOUND','钱包不存在')
    result={'wallet':{'id':str(wallet_id_value),'label':wallet.label,'wallet_address':wallet.wallet_address,'signer_address':wallet.signer_address,'wallet_type':wallet.wallet_type},'collateral_balance':None,'allowance_status':'unknown','checked_at':None,'availability':'unknown','builder_access':'unknown','diagnostic':None}
    if wallet.credential_ref and wallet.credential_ref in request.app.state.settings.wallet_credentials:
        try:
            from .trading_gateway import Gateway
            gateway=Gateway(request.app.state.settings,wallet)
            with gateway: result.update(gateway.wallet_status())
            from datetime import datetime,timezone
            result['checked_at']=datetime.now(timezone.utc).isoformat();result['availability']='allowed'
        except Exception as exc:
            code=getattr(exc,'code',None)
            code=code if isinstance(code,str) and code in WALLET_DIAGNOSTICS else 'status_unavailable'
            result['diagnostic']={'code':code,'message':WALLET_DIAGNOSTICS[code]}
            if code=='geoblock_blocked': result['availability']='blocked'
            elif 'gateway' in locals() and getattr(gateway,'geography_verified',False): result['availability']='allowed'
    else:
        result['diagnostic']={'code':'credentials_missing','message':WALLET_DIAGNOSTICS['credentials_missing']}
    return ok(request,result)
