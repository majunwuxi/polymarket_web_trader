"""平仓前的只读持仓核验，不签名、不发送卖出订单。"""
from decimal import Decimal, InvalidOperation, localcontext, ROUND_DOWN
from types import SimpleNamespace
import re
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .models import TradeIntent, MarketLink, MarketOutcome, PolyMarket, ComboRFQ
from .trading import wallet_for
from .trading_gateway import Gateway, TradingError

POSITION_MANAGER='0x006F54F7f9A22e0000CC2AB60031000000ae9fEF'


def legacy_combo_receipt_positions(settings,wallet_address,tx_hash,shares,web3_cls=None):
    """用原交易成功回执提取进入原钱包且数量足够的持仓标识。"""
    from web3 import Web3
    from hexbytes import HexBytes
    try:
        if not isinstance(tx_hash,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',tx_hash):raise ValueError('invalid transaction')
        if not isinstance(wallet_address,str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',wallet_address):raise ValueError('invalid wallet')
        with localcontext() as context:
            context.prec=90;required=Decimal(shares)*Decimal(10**6)
        if not required.is_finite() or required<=0 or required!=required.to_integral_value():raise ValueError('invalid shares')
        cls=web3_cls or Web3
        url=settings.polygon_rpc_url.get_secret_value()
        w3=cls(cls.HTTPProvider(url,request_kwargs={'timeout':10}))
        if w3.eth.chain_id!=137:raise ValueError('wrong chain')
        receipt=w3.eth.get_transaction_receipt(tx_hash)
        if receipt.status!=1 or HexBytes(receipt.transactionHash)!=HexBytes(tx_hash):raise ValueError('receipt mismatch')
        single=Web3.keccak(text='TransferSingle(address,address,address,uint256,uint256)')
        batch=Web3.keccak(text='TransferBatch(address,address,address,uint256[],uint256[])')
        received={}
        for log in receipt.logs:
            if str(log['address']).lower()!=POSITION_MANAGER.lower():continue
            topics=log['topics']
            if len(topics)!=4 or len(HexBytes(topics[2]))!=32 or len(HexBytes(topics[3]))!=32:continue
            sender=bytes(HexBytes(topics[2]))[-20:].hex();receiver=bytes(HexBytes(topics[3]))[-20:].hex()
            wallet=wallet_address[2:].lower()
            direction=int(receiver==wallet)-int(sender==wallet)
            if direction==0:continue
            signature=HexBytes(topics[0]);raw=bytes(HexBytes(log['data']))
            if signature==single:
                token,value=w3.codec.decode(['uint256','uint256'],raw)
                received[token]=received.get(token,0)+direction*value
            elif signature==batch:
                tokens,values=w3.codec.decode(['uint256[]','uint256[]'],raw)
                if len(tokens)!=len(values):raise ValueError('invalid receipt')
                for token,value in zip(tokens,values):received[token]=received.get(token,0)+direction*value
        positions={str(token) for token,value in received.items() if value>=int(required)}
        if not positions:raise ValueError('original position not verified')
        return positions
    except TradingError:raise
    except Exception as exc:
        raise TradingError('combo_close_invalid','无法从原交易回执核实组合持仓与份额') from exc


def inspect_single_position(engine, settings, ident, gateway_cls=Gateway):
    with Session(engine) as db:
        intent=db.get(TradeIntent,ident)
        if not intent:raise TradingError('not_found','订单不存在')
        if intent.side!='BUY' or intent.matched_size is None or intent.matched_size<=0:
            raise TradingError('position_unavailable','该订单没有已核实的买入成交份额')
        active=db.scalar(select(TradeIntent.id).where(TradeIntent.parent_trade_id==intent.id,TradeIntent.state.in_(['submitting','pending','partially_filled','unknown'])).limit(1))
        if active:raise TradingError('duplicate_unresolved','原订单已有未解决的平仓请求')
        sold=db.scalar(select(func.coalesce(func.sum(TradeIntent.matched_size),0)).where(TradeIntent.parent_trade_id==intent.id,TradeIntent.state.in_(['filled','cancelled'])))
        with localcontext() as context:
            context.prec=90;remaining=max(Decimal(0),intent.matched_size-sold)
        wallet=wallet_for(settings,intent.wallet_id)
        if wallet.wallet_address!=intent.wallet_address:raise TradingError('wallet_mismatch','订单钱包已变化')
        link=db.get(MarketLink,intent.market_link_id);outcome=db.get(MarketOutcome,intent.outcome_id)
        market=db.get(PolyMarket,link.market_id) if link else None
        if not market or not outcome or outcome.market_id!=market.id or link.game_id!=intent.game_id:
            raise TradingError('position_mismatch','订单与持仓市场身份不一致')
        if outcome.asset_kind not in ('position','ctf_token'):
            raise TradingError('position_mismatch','持仓资产类型无法确认')
        asset_id=outcome.asset_id
        with gateway_cls(settings,wallet) as gateway:
            try:
                book=gateway.client.get_order_book(asset_id=asset_id)
                if str(book.asset_id)!=asset_id or str(book.condition_id).lower()!=market.condition_id.lower():
                    raise TradingError('position_mismatch','持仓盘口身份不一致')
                account=gateway.client.get_balance_allowance(asset_type='CONDITIONAL-V2' if outcome.asset_kind=='position' else 'CONDITIONAL',asset_id=asset_id)
                if type(account.balance) is not int or account.balance<0:raise ValueError('invalid balance')
                with localcontext() as context:
                    context.prec=90
                    balance=Decimal(account.balance)/Decimal(10**6)
                bids=[]
                for bid in book.bids:
                    price,size=Decimal(str(bid.price)),Decimal(str(bid.size))
                    if not price.is_finite() or not size.is_finite() or not 0<price<1 or size<=0:raise ValueError('invalid book')
                    bids.append({'price':str(price),'size':str(size)})
                bids.sort(key=lambda item:Decimal(item['price']),reverse=True)
                with localcontext() as context:
                    context.prec=90
                    candidate=min(balance,remaining).quantize(Decimal('0.01'),rounding=ROUND_DOWN)
                return {'intent_id':str(intent.id),'wallet_id':str(intent.wallet_id),'wallet_address':intent.wallet_address,
                        'asset_id':asset_id,'matched_shares':str(intent.matched_size),'closed_shares':str(sold),'wallet_shares':str(balance),
                        'candidate_shares':str(candidate),
                        'bids':bids,'tick_size':str(book.tick_size),'min_order_size':str(book.min_order_size),
                        'notice':'同一资产由钱包共享；候选份额按两位小数向下取整，不代表该笔订单独立持仓的证明。'}
            except TradingError:raise
            except (InvalidOperation,ValueError,TypeError,AttributeError) as exc:
                raise TradingError('position_unavailable','无法核实持仓或盘口') from exc
            except Exception as exc:
                raise TradingError('position_unavailable','无法读取持仓或盘口') from exc


def inspect_combo_position(engine, settings, ident, web3_cls=None):
    """只读组合历史与已知持仓余额；旧记录缺少标识时返回未知。"""
    from web3 import Web3
    from .combo_quote import combo_position_id
    with Session(engine) as db:
        row=db.get(ComboRFQ,ident)
        if not row:raise TradingError('not_found','组合记录不存在')
        if row.state!='confirmed' or row.direction!='BUY' or row.quote_receive is None or row.quote_receive<=0:
            raise TradingError('position_unavailable','原组合没有已确认的买入份额')
        wallet=wallet_for(settings,row.wallet_id)
        if wallet.wallet_address!=row.wallet_address:raise TradingError('wallet_mismatch','原组合钱包已变化')
        active=db.scalar(select(ComboRFQ.id).where(ComboRFQ.parent_combo_id==row.id,ComboRFQ.state.in_(['requesting','quoted','accepting','executing','unknown'])).limit(1))
        if active:raise TradingError('duplicate_unresolved','原组合已有未解决的平仓请求')
        sold=db.scalar(select(func.coalesce(func.sum(ComboRFQ.quantity),0)).where(ComboRFQ.parent_combo_id==row.id,ComboRFQ.state=='confirmed'))
        with localcontext() as context:
            context.prec=90;remaining=max(Decimal(0),row.quote_receive-sold)
        position=combo_position_id(SimpleNamespace(position_id=row.combo_position_id)) if row.combo_position_id else None
        address=row.wallet_address;wallet_id=str(row.wallet_id)
    balance=None;approved=None
    if position:
        try:
            cls=web3_cls or Web3
            url=settings.polygon_rpc_url.get_secret_value()
            w3=cls(cls.HTTPProvider(url,request_kwargs={'timeout':10}))
            if w3.eth.chain_id!=137:raise ValueError('wrong chain')
            abi=[{'type':'function','name':'balanceOf','stateMutability':'view','inputs':[{'name':'account','type':'address'},{'name':'id','type':'uint256'}],'outputs':[{'type':'uint256'}]},{'type':'function','name':'isApprovedForAll','stateMutability':'view','inputs':[{'name':'account','type':'address'},{'name':'operator','type':'address'}],'outputs':[{'type':'bool'}]}]
            contract=w3.eth.contract(address=cls.to_checksum_address('0x006F54F7f9A22e0000CC2AB60031000000ae9fEF'),abi=abi)
            account=cls.to_checksum_address(address);exchange=cls.to_checksum_address('0xe3333700cA9d93003F00f0F71f8515005F6c00Aa')
            raw=contract.functions.balanceOf(account,int(position)).call()
            approval=contract.functions.isApprovedForAll(account,exchange).call()
            if type(raw) is not int or raw<0 or type(approval) is not bool:raise ValueError('invalid balance')
            with localcontext() as context:
                context.prec=90;balance=Decimal(raw)/Decimal(10**6)
            approved=approval
        except Exception as exc:raise TradingError('position_unavailable','无法核实组合持仓余额与授权') from exc
    candidate=min(remaining,balance) if balance is not None else remaining
    return {'wallet_id':wallet_id,'wallet_address':address,'position_id':position,'wallet_shares':str(balance) if balance is not None else None,
            'previously_closed_shares':str(sold),'candidate_shares':str(candidate),'approved':approved,
            'notice':'同一组合的份额由钱包共享。旧记录若无持仓标识，实际余额将在接受报价前按真实报价资产核验。'}
