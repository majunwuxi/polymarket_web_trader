from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
import math

import httpx
from eth_account import Account
from web3 import Web3
from polymarket import SecureClient, RequestRejectedError
from polymarket.models.clob.api_key import ApiKeyCreds
from polymarket.environments import PRODUCTION
from polymarket._internal.actions.orders.context import resolve_order_exchange_address
from polymarket._internal.protocol import is_v2_position_id
from .gamma import parse_market
from .sports_sources import timestamp


class TradingError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None=None):
        self.code,self.message,self.retry_after=code,message,retry_after
        super().__init__(message)


class NoDeployClient(SecureClient):
    # SDK 0.18.0 的 create 会在特定情况下调用此方法；覆盖以禁止隐式部署。
    def _deploy_default_deposit_wallet(self):
        raise TradingError('wallet_undeployed','账户钱包未部署，不能自动部署')


def finite_decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value,str): raise TradingError('invalid_amount',field+'必须使用十进制字符串')
    try: number=Decimal(value)
    except (InvalidOperation,ValueError): raise TradingError('invalid_amount',field+'格式无效')
    if not number.is_finite() or number<=0 or number.adjusted()>=20 or number.as_tuple().exponent < -18:
        raise TradingError('invalid_amount',field+'超出有效精度或范围')
    return number


def validate_rules(price: Decimal, size: Decimal, tick: Decimal, minimum: Decimal) -> None:
    try:
        if not tick.is_finite() or not minimum.is_finite() or not 0<tick<Decimal('0.5') or minimum<=0: raise TradingError('book_unavailable','订单簿规则无效')
        with localcontext() as ctx:
            ctx.prec=80
            if price<tick or price>Decimal(1)-tick or price%tick: raise TradingError('price_tick_invalid','价格不符合市场增量或边界')
            if size<minimum or size%Decimal('0.01'): raise TradingError('size_invalid','份额低于最小值或超过两位小数')
    except InvalidOperation as exc:
        raise TradingError('invalid_amount','价格或份额无效') from exc


def restriction(exc: Exception) -> TradingError | None:
    if isinstance(exc,RequestRejectedError):
        code=None
        if exc.status==425 or exc.restriction=='restarting': code='engine_restarting'
        elif exc.restriction=='post_only' or exc.code=='post_only_mode': code='post_only_mode'
        if code:
            try: delay=max(0,math.ceil(float(exc.retry_after))) if exc.retry_after is not None and math.isfinite(float(exc.retry_after)) else None
            except (ValueError,TypeError,OverflowError): delay=None
            return TradingError(code,'撮合引擎重启' if code=='engine_restarting' else '市场处于 post-only 限制',delay)
    return None


def ensure_geography() -> None:
    try:
        response=httpx.get('https://polymarket.com/api/geoblock',timeout=10,follow_redirects=False)
        if response.status_code!=200: raise TradingError('geoblock_unknown','无法确认地区交易限制')
        body=response.json()
        if not isinstance(body,dict) or type(body.get('blocked')) is not bool: raise TradingError('geoblock_unknown','无法确认地区交易限制')
        if body['blocked']: raise TradingError('geoblock_blocked','当前服务位置受交易限制')
    except TradingError: raise
    except Exception as exc: raise TradingError('geoblock_unknown','无法确认地区交易限制') from exc


class Gateway:
    def __init__(self, settings, wallet):
        self.settings=settings;self.wallet=wallet;self.client=None

    def __enter__(self):
        ensure_geography()
        ref=self.wallet.credential_ref
        credential=self.settings.wallet_credentials.get(ref) if ref else None
        if credential is None: raise TradingError('credentials_missing','钱包未配置有效凭据')
        try: signer=Account.from_key(credential.private_key.get_secret_value()).address.lower()
        except Exception as exc: raise TradingError('wallet_invalid','签名私钥无效') from exc
        if signer!=self.wallet.signer_address: raise TradingError('signer_mismatch','私钥与配置签名者不一致')
        if self.wallet.wallet_type=='EOA':
            if signer!=self.wallet.wallet_address: raise TradingError('wallet_mismatch','EOA 钱包与签名者不一致')
        else:
            if signer==self.wallet.wallet_address: raise TradingError('wallet_mismatch','智能钱包不能与签名者相同')
            if not self.settings.polygon_rpc_url: raise TradingError('wallet_deployment_unknown','未配置 Polygon RPC，无法核对钱包部署')
            try:
                web3=Web3(Web3.HTTPProvider(self.settings.polygon_rpc_url.get_secret_value(),request_kwargs={'timeout':10}))
                if web3.eth.chain_id!=137 or not web3.eth.get_code(Web3.to_checksum_address(self.wallet.wallet_address)):
                    raise TradingError('wallet_undeployed','钱包未部署或 RPC 链不正确')
            except TradingError: raise
            except Exception as exc: raise TradingError('wallet_deployment_unknown','无法核对钱包部署状态') from exc
        vals=[x.get_secret_value() if x else '' for x in (credential.clob_api_key,credential.clob_api_secret,credential.clob_api_passphrase)]
        creds=ApiKeyCreds(apiKey=vals[0],secret=vals[1],passphrase=vals[2]) if all(vals) else None
        try:
            self.client=NoDeployClient.create(private_key=credential.private_key.get_secret_value(),wallet=Web3.to_checksum_address(self.wallet.wallet_address),credentials=creds)
            if str(self.client.wallet).lower()!=self.wallet.wallet_address or str(self.client.wallet_type)!=self.wallet.wallet_type:
                raise TradingError('wallet_mismatch','SDK 钱包身份或类型不匹配')
        except TradingError:
            if self.client: self.client.close()
            raise
        except Exception as exc:
            if self.client: self.client.close()
            raise restriction(exc) or TradingError('sdk_unavailable','交易客户端初始化失败') from exc
        return self

    def __exit__(self,*args):
        if self.client: self.client.close()

    def inspect(self, market, outcome, side, price, size, event):
        try:
            response=httpx.get('https://gamma-api.polymarket.com/events/'+event.gamma_event_id,timeout=10,follow_redirects=False)
            if response.status_code!=200: raise TradingError('market_unavailable','无法实时核对市场')
            raw=response.json()
            if not isinstance(raw,dict) or raw.get('id')!=event.gamma_event_id: raise TradingError('market_unavailable','市场来源身份不一致')
            tags={x.get('slug') for x in raw.get('tags',[]) if isinstance(x,dict)}
            if ('nba' if event.sport=='nba' else 'soccer') not in tags: raise TradingError('market_unavailable','市场运动类型不一致')
            teams=raw.get('teams');teams=teams if isinstance(teams,list) else []
            home=next((x.get('name') for x in teams if isinstance(x,dict) and x.get('ordering')=='home'),None)
            away=next((x.get('name') for x in teams if isinstance(x,dict) and x.get('ordering')=='away'),None)
            if home!=event.home_name or away!=event.away_name or timestamp(raw.get('startTime'))!=event.starts_at: raise TradingError('market_unavailable','市场赛事信息已变更')
            rows=raw.get('markets'); rows=rows if isinstance(rows,list) else []
            chosen=next((x for x in rows if isinstance(x,dict) and x.get('id')==market.gamma_market_id),None)
            if not chosen: raise TradingError('market_unavailable','市场已不可用')
            live=parse_market(chosen)
            if live.fingerprint!=market.fingerprint or live.accepting_orders is not True or live.condition_id!=market.condition_id or live.version!=market.protocol_version or not any(x.index==outcome.outcome_index and x.asset_id==outcome.asset_id and x.kind==outcome.asset_kind for x in live.outcomes):
                raise TradingError('market_unavailable','市场或资产信息已变更')
        except TradingError: raise
        except Exception as exc: raise TradingError('market_unavailable','无法实时核对市场') from exc
        if (outcome.asset_kind=='position') != bool(is_v2_position_id(outcome.asset_id)):
            raise TradingError('asset_version_mismatch','资产与官方 SDK 识别的协议不一致')
        try:
            book=self.client.get_order_book(asset_id=outcome.asset_id)
            if str(book.asset_id)!=outcome.asset_id or str(book.condition_id).lower()!=market.condition_id: raise TradingError('book_mismatch','盘口与选定市场不一致')
            tick,minimum=Decimal(book.tick_size),Decimal(book.min_order_size)
            validate_rules(price,size,tick,minimum)
            asset_type='COLLATERAL' if side=='BUY' else ('CONDITIONAL-V2' if outcome.asset_kind=='position' else 'CONDITIONAL')
            account=self.client.get_balance_allowance(asset_type=asset_type,**({'asset_id':outcome.asset_id} if side=='SELL' else {}))
            with localcontext() as ctx:
                ctx.prec=80
                required=(price*size if side=='BUY' else size)*Decimal(10**6)
            spender=str(resolve_order_exchange_address(PRODUCTION._config,asset_id=outcome.asset_id,neg_risk=book.neg_risk)).lower()
            allowances={k.lower():v for k,v in account.allowances.items()}
            allowance=allowances.get(spender)
            if not isinstance(account.balance,int) or allowance is None or not isinstance(allowance,int): raise TradingError('allowance_unknown','余额或授权无法验证')
            if Decimal(account.balance)<required: raise TradingError('balance_insufficient','余额不足')
            if Decimal(allowance)<required: raise TradingError('allowance_insufficient','授权不足，请在外部完成授权')
            return {'hash':str(book.hash),'tick':str(tick),'minimum':str(minimum),'neg_risk':book.neg_risk,'bids':[{'price':str(x.price),'size':str(x.size)} for x in book.bids],'asks':[{'price':str(x.price),'size':str(x.size)} for x in book.asks],'balance':str(Decimal(account.balance)/Decimal(10**6)),'allowance_status':'ready'}
        except TradingError: raise
        except Exception as exc: raise restriction(exc) or TradingError('book_or_balance_unavailable','无法验证盘口、余额或授权') from exc

    def wallet_status(self):
        try:
            account=self.client.get_balance_allowance(asset_type='COLLATERAL')
            if not isinstance(account.balance,int) or account.balance<0 or not isinstance(account.allowances,dict): raise ValueError('invalid balance')
            with localcontext() as ctx:
                ctx.prec=80
                balance=format(Decimal(account.balance)/Decimal(10**6),'f')
            return {'collateral_balance':balance,'allowance_status':'unknown'}
        except Exception as exc:
            raise restriction(exc) or TradingError('balance_unavailable','余额或授权暂不可用') from exc

    def sign(self,outcome,side,price,size):
        try:
            signed=self.client.create_limit_order(asset_id=outcome.asset_id,side=side,price=str(price),size=str(size))
            with localcontext() as ctx:
                ctx.prec=80
                shares=size*Decimal(10**6);notional=price*size*Decimal(10**6)
                if shares!=shares.to_integral_value() or notional!=notional.to_integral_value(): raise TradingError('signed_order_mismatch','订单金额无法精确表示，未发送')
                maker,taker=(int(notional),int(shares)) if side=='BUY' else (int(shares),int(notional))
            wallet_type={'EOA':0,'POLY_PROXY':1,'GNOSIS_SAFE':2,'DEPOSIT_WALLET':3}[self.wallet.wallet_type]
            expected_signer=self.wallet.wallet_address if self.wallet.wallet_type=='DEPOSIT_WALLET' else self.wallet.signer_address
            if (str(signed.token_id)!=outcome.asset_id or str(signed.side).upper()!=side or str(signed.maker).lower()!=self.wallet.wallet_address or str(signed.signer).lower()!=expected_signer or signed.signature_type!=wallet_type or signed.maker_amount!=maker or signed.taker_amount!=taker or signed.order_type!='GTC' or signed.expiration!=0 or signed.post_only):
                raise TradingError('signed_order_mismatch','签名订单与确认参数不一致，未发送')
            return signed
        except TradingError: raise
        except Exception as exc: raise restriction(exc) or TradingError('sign_failed','订单签名失败，未发送') from exc

    def post(self,signed):
        # 不使用 place_limit_order：该快捷方法可能自动授权并重试。
        return self.client.post_order(signed)

    def get_order(self,order_id): return self.client.get_order(order_id=order_id)
    def fills(self,asset_id,condition_id,order_id,side,wallet_address,requested_size):
        items=[];seen=set();truncated=False
        for number,page in enumerate(self.client.list_account_trades(asset_id=asset_id,market=condition_id)):
            for trade in page.items:
                if str(trade.condition_id).lower()!=condition_id: continue
                value=None
                if str(trade.taker_order_id).lower()==order_id.lower() and str(trade.asset_id)==asset_id and str(trade.side).upper()==side:
                    value=(trade.size,trade.price)
                else:
                    matched=[x for x in trade.maker_orders if str(x.order_id).lower()==order_id.lower() and str(x.asset_id)==asset_id and str(x.maker_address).lower()==wallet_address and str(x.side).upper()==side]
                    if len(matched)>1: raise TradingError('fill_ambiguous','成交记录不明确')
                    if matched:value=(matched[0].matched_amount,matched[0].price)
                if value is None: continue
                ident=str(trade.id)
                if not ident or len(ident)>255 or ident in seen:raise TradingError('fill_ambiguous','成交记录不明确')
                amount=finite_decimal(str(value[0]),'成交份额');price=finite_decimal(str(value[1]),'成交价格')
                if amount>requested_size or price>=1 or trade.matched_at.tzinfo is None:raise TradingError('fill_invalid','成交记录无效')
                seen.add(ident);items.append({'trade_id':ident,'size':amount,'price':price,'status':str(trade.status),'matched_at':trade.matched_at})
            if number>=9:
                truncated=bool(page.has_more)
                break
        return items,truncated

    def candidate_orders(self,asset_id):
        found=[];truncated=False
        for number,page in enumerate(self.client.list_open_orders(asset_id=asset_id)):
            found.extend(page.items)
            if number>=4:
                truncated=bool(page.has_more)
                break
        return found,truncated
    def cancel(self,order_id): return self.client.cancel_order(order_id=order_id)
