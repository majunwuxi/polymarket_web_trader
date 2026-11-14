"""组合平仓身份与额度验证，全部使用模拟数据。"""
import uuid
from decimal import Decimal
from types import SimpleNamespace as S
import pytest
from backend.combo_service import verify_combo_close_origin
from backend.trading_gateway import TradingError


def setup_case(active=None,sold=Decimal('0')):
    parent=uuid.uuid4();wallet=uuid.uuid4();link1,link2,out1,out2=[uuid.uuid4() for _ in range(4)]
    origin=S(id=parent,state='confirmed',direction='BUY',wallet_id=wallet,quote_receive=Decimal('5'))
    legs=[S(market_link_id=link1,outcome_id=out1,position_id='11'),S(market_link_id=link2,outcome_id=out2,position_id='22')]
    class Db:
        def get(self,*args):return origin
        def scalars(self,*args):return legs
        def scalar(self,query):return sold if 'sum(' in str(query) else active
    return Db(),origin,[(str(x.market_link_id),str(x.outcome_id)) for x in legs],{out1:'11',out2:'22'}


def test_close_requires_original_legs_and_exact_position_identity():
    db,origin,legs,positions=setup_case()
    assert verify_combo_close_origin(db,origin.id,origin.wallet_id,legs,Decimal('5'),positions) is origin
    with pytest.raises(TradingError):verify_combo_close_origin(db,origin.id,origin.wallet_id,legs[:1],Decimal('1'),positions)
    positions[next(iter(positions))]='999'
    with pytest.raises(TradingError):verify_combo_close_origin(db,origin.id,origin.wallet_id,legs,Decimal('1'),positions)


def test_close_rejects_unresolved_or_previously_sold_shares():
    db,origin,legs,positions=setup_case(active='mock-active')
    with pytest.raises(TradingError,match='未解决'):verify_combo_close_origin(db,origin.id,origin.wallet_id,legs,Decimal('1'),positions)
    db,origin,legs,positions=setup_case(sold=Decimal('4'))
    with pytest.raises(TradingError,match='超过'):verify_combo_close_origin(db,origin.id,origin.wallet_id,legs,Decimal('2'),positions)
