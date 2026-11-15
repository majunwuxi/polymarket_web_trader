"""组合平仓身份与额度验证，全部使用模拟数据。"""
import uuid
from decimal import Decimal
from types import SimpleNamespace as S
import pytest
from backend import combo_service
from backend.combo_service import verify_combo_close_origin
from backend.models import Game, MarketLink, MarketOutcome, PolyMarket
from backend.trading_gateway import TradingError


def setup_case(active=None,sold=Decimal('0'),result_verified=False):
    parent=uuid.uuid4();wallet=uuid.uuid4();link1,link2,out1,out2=[uuid.uuid4() for _ in range(4)]
    origin=S(id=parent,state='confirmed',direction='BUY',wallet_id=wallet,quote_receive=Decimal('5'))
    legs=[S(market_link_id=link1,outcome_id=out1,position_id='11'),S(market_link_id=link2,outcome_id=out2,position_id='22')]
    class Db:
        def get(self,model,ident):
            if model is Game:
                return S(id=ident,status='finished' if result_verified else 'scheduled',
                         home_score=100 if result_verified else None,away_score=90 if result_verified else None)
            if model is MarketLink:return S(game_id=ident)
            return origin
        def scalars(self,*args):return legs
        def scalar(self,query):
            text=str(query)
            if 'result_binding_requests' in text.lower():return S(state='bound') if result_verified else None
            return sold if 'sum(' in text else active
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


def test_close_rejected_when_any_leg_result_confirmed():
    """任一腿赛果已确认即组合胜负已定，平仓链路必须拒绝。"""
    db,origin,legs,positions=setup_case(result_verified=True)
    with pytest.raises(TradingError,match='不能平仓'):
        verify_combo_close_origin(db,origin.id,origin.wallet_id,legs,Decimal('1'),positions)


def legs_case(mismatch=False,missing_market=False,duplicate=False):
    market=S(id=uuid.uuid4())
    other=S(id=market.id if duplicate else uuid.uuid4())
    link1=S(id=uuid.uuid4(),market_id=market.id,game_id=uuid.uuid4())
    outcome1=S(id=uuid.uuid4(),market_id=other.id if mismatch else market.id)
    link2=S(id=uuid.uuid4(),market_id=other.id,game_id=uuid.uuid4())
    outcome2=S(id=uuid.uuid4(),market_id=other.id)
    rows={MarketLink:{link1.id:link1,link2.id:link2},MarketOutcome:{outcome1.id:outcome1,outcome2.id:outcome2},
          PolyMarket:{market.id:market,other.id:None if missing_market else other}}
    class Db:
        def get(self,model,ident):return rows.get(model,{}).get(ident)
    return Db(),[(str(link1.id),str(outcome1.id)),(str(link2.id),str(outcome2.id))],(link1,market,outcome1)


def test_open_still_requires_confirmed_event_mapping(monkeypatch):
    db,legs,_=legs_case()
    monkeypatch.setattr(combo_service,'valid_link',lambda *args,**kwargs:False)
    with pytest.raises(TradingError,match='赛事映射'):combo_service.resolve_legs(db,legs)
    with pytest.raises(TradingError,match='赛事映射'):combo_service.resolve_legs(db,legs,False)


def test_combo_close_ignores_stale_event_mapping(monkeypatch):
    db,legs,expected=legs_case()
    monkeypatch.setattr(combo_service,'valid_link',lambda *args,**kwargs:False)
    result=combo_service.resolve_legs(db,legs,True)
    assert len(result)==2 and result[0]==expected


def test_combo_close_still_checks_market_asset_identity(monkeypatch):
    monkeypatch.setattr(combo_service,'valid_link',lambda *args,**kwargs:True)
    for kwargs in ({'mismatch':True},{'missing_market':True}):
        db,legs,_=legs_case(**kwargs)
        with pytest.raises(TradingError,match='映射|市场'):combo_service.resolve_legs(db,legs,True)
    db,legs,_=legs_case(duplicate=True)
    with pytest.raises(TradingError,match='重复'):combo_service.resolve_legs(db,legs,True)
