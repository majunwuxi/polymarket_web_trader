"""只读平仓上下文测试；数据与客户端均为模拟。"""
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
from types import SimpleNamespace as S
import pytest

from backend import close_positions as module
from backend.models import TradeIntent, MarketLink, MarketOutcome, PolyMarket, ComboRFQ
from backend.trading_gateway import TradingError
from backend.trading import normalize, verify_close_origin


def context(monkeypatch, *, side='BUY', matched=Decimal('3'), balance=2_000_000, book_asset='123'):
    intent=S(id='mock-intent',side=side,matched_size=matched,wallet_id='mock-wallet',wallet_address='0xmock',market_link_id='link',outcome_id='outcome',game_id='game')
    link=S(market_id='market',game_id='game')
    outcome=S(market_id='market',asset_kind='position',asset_id='123')
    market=S(id='market',condition_id='0xcondition')
    objects={TradeIntent:intent,MarketLink:link,MarketOutcome:outcome,PolyMarket:market}
    class Db:
        def __init__(self,*args):self.calls=0
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,model,ident):return objects[model]
        def scalar(self,query):
            self.calls+=1
            return None if self.calls==1 else Decimal(0)
    class Gateway:
        def __init__(self,*args):self.client=self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get_order_book(self,**kwargs):return S(asset_id=book_asset,condition_id='0xcondition',bids=[S(price='0.4',size='5'),S(price='0.6',size='1')],tick_size='0.01',min_order_size='1')
        def get_balance_allowance(self,**kwargs):return S(balance=balance)
    monkeypatch.setattr(module,'Session',Db)
    monkeypatch.setattr(module,'wallet_for',lambda *args:S(wallet_address='0xmock'))
    return lambda:module.inspect_single_position(None,None,'mock-intent',gateway_cls=Gateway)


def test_read_only_context_caps_by_both_wallet_and_matched_shares(monkeypatch):
    result=context(monkeypatch)()
    assert result['wallet_shares']=='2' and result['candidate_shares']=='2.00'
    assert [x['price'] for x in result['bids']]==['0.6','0.4']
    assert '不代表该笔订单独立持仓的证明' in result['notice']


@pytest.mark.parametrize('side,matched',[('SELL',Decimal('3')),('BUY',None),('BUY',Decimal('0'))])
def test_no_verified_buy_fill_means_no_close_context(monkeypatch,side,matched):
    with pytest.raises(TradingError,match='没有已核实'):
        context(monkeypatch,side=side,matched=matched)()


def test_mismatched_book_is_rejected(monkeypatch):
    with pytest.raises(TradingError,match='身份不一致'):
        context(monkeypatch,book_asset='other')()


def test_fok_close_origin_requires_matching_buy_and_bounded_size():
    body=S(side='SELL',limit_price='0.4',size='2',wallet_id='wallet',market_link_id='link',outcome_id='outcome',order_type='FOK',parent_trade_id='parent')
    original=S(id='parent',side='BUY',matched_size=Decimal('3'),wallet_id='wallet',market_link_id='link',outcome_id='outcome')
    db=S(get=lambda model,ident:original,scalar=lambda query:Decimal(0) if 'sum(' in str(query) else None)
    data,_,size=normalize(body)
    assert data['order_type']=='FOK' and data['parent_trade_id']=='parent'
    verify_close_origin(db,body,size)
    with pytest.raises(TradingError):verify_close_origin(db,body,Decimal('4'))
    original.outcome_id='other'
    with pytest.raises(TradingError):verify_close_origin(db,body,size)


def test_fok_changes_only_submission_type_after_validated_signature(monkeypatch):
    from backend.trading_gateway import Gateway
    @dataclass(frozen=True)
    class Signed:
        order_type:str='GTC'
        signature:str='mock-signature'
    gateway=Gateway(None,None)
    signed=Signed()
    monkeypatch.setattr(gateway,'sign',lambda *args:signed)
    result=gateway.sign_fok(S(),'SELL',Decimal('.4'),Decimal('2'))
    assert result.order_type=='FOK' and result.signature==signed.signature and signed.order_type=='GTC'
    with pytest.raises(TradingError):gateway.sign_fok(S(),'BUY',Decimal('.4'),Decimal('2'))


def test_fok_is_present_in_exchange_post_envelope():
    from polymarket.models.clob.orders import SignedOrder
    from polymarket._internal.actions.orders.post import build_post_order_request
    order=SignedOrder(builder='0x'+'0'*64,expiration=0,maker='0x'+'1'*40,maker_amount=2_000_000,
                      metadata='0x'+'0'*64,order_type='FOK',salt=1,side='SELL',signature='0x1234',
                      signature_type=3,signer='0x'+'1'*40,taker_amount=800_000,timestamp=1,token_id='123')
    path,payload=build_post_order_request(order,owner_api_key='mock')
    assert path=='/order' and payload['orderType']=='FOK'
    assert payload['order']['side']=='SELL' and payload['order']['makerAmount']=='2000000'


def test_existing_unresolved_close_or_sold_shares_block_extra_sales():
    body=S(side='SELL',limit_price='0.4',size='2',wallet_id='wallet',market_link_id='link',outcome_id='outcome',order_type='FOK',parent_trade_id='parent')
    original=S(id='parent',side='BUY',matched_size=Decimal('3'),wallet_id='wallet',market_link_id='link',outcome_id='outcome')
    db=S(get=lambda model,ident:original,scalar=lambda query:'pending')
    with pytest.raises(TradingError,match='未解决'):verify_close_origin(db,body,Decimal('2'))
    db.scalar=lambda query:Decimal('2') if 'sum(' in str(query) else None
    with pytest.raises(TradingError,match='未平仓'):verify_close_origin(db,body,Decimal('2'))


@pytest.mark.parametrize('position',[None,'123'])
def test_combo_close_context_uses_real_balance_when_identity_known(monkeypatch,position):
    row=S(id='mock-combo',state='confirmed',direction='BUY',quote_receive=Decimal('5'),wallet_id='wallet',wallet_address='0xmock',combo_position_id=position)
    class Db:
        def __init__(self,*args):self.calls=0
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,model,ident):assert model is ComboRFQ;return row
        def scalar(self,query):self.calls+=1;return None if self.calls==1 else Decimal('1')
    class Call:
        def __init__(self,value):self.value=value
        def call(self):return self.value
    class Functions:
        def balanceOf(self,*args):return Call(2_000_000)
        def isApprovedForAll(self,*args):return Call(True)
    class Web3Mock:
        def __init__(self,*args):self.eth=S(chain_id=137,contract=lambda **kw:S(functions=Functions()))
        @staticmethod
        def HTTPProvider(*args,**kwargs):return object()
        @staticmethod
        def to_checksum_address(value):return value
    monkeypatch.setattr(module,'Session',Db)
    monkeypatch.setattr(module,'wallet_for',lambda *args:S(wallet_address='0xmock'))
    settings=S(polygon_rpc_url=S(get_secret_value=lambda:'http://mock.invalid'))
    result=module.inspect_combo_position(None,settings,'mock-combo',web3_cls=Web3Mock)
    assert result['previously_closed_shares']=='1'
    assert result['candidate_shares']==('2' if position else '4')
    assert result['wallet_shares']==('2' if position else None)


def test_combo_close_quote_must_cover_full_requested_quantity_and_identity():
    from backend.combo_service import verify_combo_close_quote
    quote=S(total_required=Decimal('2'),position_id='123')
    verify_combo_close_quote(quote,Decimal('2'),'123')
    with pytest.raises(TradingError):verify_combo_close_quote(quote,Decimal('3'),'123')
    with pytest.raises(TradingError):verify_combo_close_quote(quote,Decimal('2'),'456')


def test_legacy_combo_receipt_requires_successful_incoming_position_and_net_shares():
    from eth_abi import encode
    from hexbytes import HexBytes
    from web3 import Web3
    wallet='0x'+'11'*20;other='0x'+'22'*20;tx='0x'+'aa'*32
    topic=lambda address:HexBytes('0x'+'00'*12+address[2:])
    signature=Web3.keccak(text='TransferSingle(address,address,address,uint256,uint256)')
    def event(sender,receiver,value):
        return {'address':module.POSITION_MANAGER,'topics':[signature,topic(other),topic(sender),topic(receiver)],'data':encode(['uint256','uint256'],[123,value])}
    receipt=S(status=1,transactionHash=HexBytes(tx),logs=[event(other,wallet,3_000_000),event(wallet,other,1_000_000)])
    class Web3Mock:
        def __init__(self,*args):self.eth=S(chain_id=137,get_transaction_receipt=lambda ident:receipt);self.codec=Web3().codec
        @staticmethod
        def HTTPProvider(*args,**kwargs):return object()
    settings=S(polygon_rpc_url=S(get_secret_value=lambda:'http://mock.invalid'))
    assert module.legacy_combo_receipt_positions(settings,wallet,tx,Decimal('2'),web3_cls=Web3Mock)=={'123'}
    with pytest.raises(TradingError):module.legacy_combo_receipt_positions(settings,wallet,tx,Decimal('2.01'),web3_cls=Web3Mock)
    receipt.status=0
    with pytest.raises(TradingError):module.legacy_combo_receipt_positions(settings,wallet,tx,Decimal('1'),web3_cls=Web3Mock)


def test_fok_timeout_persists_unknown_and_never_reposts(monkeypatch):
    """模拟完整执行边界；不连接数据库和交易所。"""
    from backend import trading
    from backend.models import Game
    parent_id,wallet_id,link_id,outcome_id,game_id,market_id=[uuid.uuid4() for _ in range(6)]
    body=S(wallet_id=wallet_id,market_link_id=link_id,outcome_id=outcome_id,side='SELL',limit_price='0.4',size='2',order_type='FOK',parent_trade_id=parent_id,preview_token='mock')
    original=S(id=parent_id,side='BUY',matched_size=Decimal('3'),wallet_id=wallet_id,market_link_id=link_id,outcome_id=outcome_id)
    now=datetime(2026,1,1,tzinfo=timezone.utc);seen=[];saved=[]
    class Db:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def begin(self):return self
        def get(self,model,ident):return original if model is TradeIntent else S(provider='polymarket') if model is Game else None
        def scalar(self,query):return Decimal(0) if 'sum(' in str(query) else None
        def add(self,value):seen.append(value)
        def flush(self):pass
    class Gateway:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def inspect(self,*args):return {'hash':'mock-book'}
        def sign_fok(self,*args):saved.append('signed-fok');return 'mock-signed'
        def post(self,signed):saved.append('posted');raise TimeoutError('mock')
    link=S(id=link_id,game_id=game_id,market_id=market_id)
    monkeypatch.setattr(trading,'Session',Db)
    monkeypatch.setattr(trading,'wallet_for',lambda *args:S(wallet_address='0xmock'))
    monkeypatch.setattr(trading,'resolve',lambda *args:(link,S(),S()))
    monkeypatch.setattr(trading,'event_for_inspection',lambda *args:None)
    monkeypatch.setattr(trading,'db_now',lambda db:now)
    data,_,_=trading.normalize(body)
    monkeypatch.setattr(trading,'decode_token',lambda *args:{'fingerprint':trading.fingerprint(data),'book_hash':'mock-book','expires_at':int(now.timestamp())+30})
    monkeypatch.setattr(trading,'save_result',lambda engine,ident,state,code=None,**kw:saved.append((state,code)))
    trading.execute(None,None,body,uuid.uuid4(),gateway_cls=Gateway)
    created=next(item for item in seen if isinstance(item,TradeIntent))
    assert created.order_type=='FOK' and created.parent_trade_id==parent_id
    assert saved==['signed-fok','posted',('unknown','submission_unknown')]
