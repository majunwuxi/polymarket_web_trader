"""报价值为模拟数据，不触发资金交易；一步下单编排使用纯 mock，不连接数据库或上游。"""
from decimal import Decimal
from types import SimpleNamespace as S
from unittest.mock import MagicMock, patch
import pytest
from backend import combo_api
from backend.combo_quote import verify_quote, combo_position_id
from backend.trading_gateway import TradingError


def quote(**kwargs):
    data=dict(rfq_id='mock-rfq',direction='BUY',expires_at=2000,blended_price=Decimal('0.5'),maker_amount=Decimal('9'),taker_amount=Decimal('18'),total_required=Decimal('10'),net_receive=None)
    return S(**(data|kwargs))


def test_valid_and_expired_quote():
    verify_quote(quote(),rfq_id='mock-rfq',direction='BUY',quantity=Decimal('10'),now_ms=1999)
    with pytest.raises(TradingError):verify_quote(quote(),rfq_id='mock-rfq',direction='BUY',quantity=Decimal('10'),now_ms=2000)


def test_wrong_rfq_and_budget_are_rejected():
    for value in (quote(rfq_id='other'),quote(total_required=Decimal('10.01')),quote(blended_price=Decimal('NaN'))):
        with pytest.raises(TradingError):verify_quote(value,rfq_id='mock-rfq',direction='BUY',quantity=Decimal('10'),now_ms=1000)


def test_sell_requires_proceeds_and_correct_shares():
    value=quote(direction='SELL',maker_amount=Decimal('5'),total_required=Decimal('5'),net_receive=Decimal('2'))
    verify_quote(value,rfq_id='mock-rfq',direction='SELL',quantity=Decimal('5'),now_ms=1000)
    for value in (quote(direction='SELL',total_required=Decimal('6'),net_receive=Decimal('2')),quote(direction='SELL',net_receive=None)):
        with pytest.raises(TradingError):verify_quote(value,rfq_id='mock-rfq',direction='SELL',quantity=Decimal('5'),now_ms=1000)


def test_combo_position_identity_must_be_uint256_digits():
    assert combo_position_id(quote(position_id='123456'))=='123456'
    for bad in ('', '-1', 'abc', '１', str(2**256)):
        with pytest.raises(TradingError):combo_position_id(quote(position_id=bad))


def fake_request(engine):
    return S(app=S(state=S(engine=engine,settings=object())))


def test_auto_accept_requires_a_newly_quoted_row():
    engine=object();req=fake_request(engine)
    with patch.object(combo_api,'accept_quote') as accept:
        combo_api.auto_accept_current_quote(req,'mock-id',False)
        accept.assert_not_called()  # 幂等重放绝不再次签名下单
    for state,quote_id in (('failed',None),('quoted',None),('executing','mock-quote')):
        session=MagicMock();session.__enter__.return_value.get.return_value=S(state=state,quote_id=quote_id)
        with patch.object(combo_api,'Session',return_value=session),patch.object(combo_api,'accept_quote') as accept:
            combo_api.auto_accept_current_quote(req,'mock-id',True)
            accept.assert_not_called()


def test_auto_accept_signs_the_current_quote_once():
    engine=object();req=fake_request(engine)
    session=MagicMock();session.__enter__.return_value.get.return_value=S(state='quoted',quote_id='mock-quote')
    with patch.object(combo_api,'Session',return_value=session),patch.object(combo_api,'accept_quote') as accept:
        combo_api.auto_accept_current_quote(req,'mock-id',True)
        accept.assert_called_once_with(engine,req.app.state.settings,'mock-id','mock-quote')


def test_auto_accept_failure_closes_quote_and_reraises():
    engine=object();req=fake_request(engine)
    session=MagicMock();session.__enter__.return_value.get.return_value=S(state='quoted',quote_id='mock-quote')
    error=TradingError('allowance_insufficient','组合交易授权不足')
    with patch.object(combo_api,'Session',return_value=session),patch.object(combo_api,'accept_quote',side_effect=error),patch.object(combo_api,'discard_unaccepted_quote') as discard:
        with pytest.raises(TradingError):combo_api.auto_accept_current_quote(req,'mock-id',True)
        discard.assert_called_once_with(engine,'mock-id','allowance_insufficient')


def test_combo_body_auto_accept_defaults_off():
    import uuid
    legs=[{'market_link_id':uuid.uuid4(),'outcome_id':uuid.uuid4()} for _ in range(2)]
    body=combo_api.ComboBody(wallet_id=uuid.uuid4(),direction='BUY',quantity='10',legs=legs)
    assert body.auto_accept is False
    assert body.model_copy(update={'auto_accept':True}).auto_accept is True


def test_rfq_failure_code_keeps_the_specific_upstream_reason():
    from backend.combo_service import rfq_failure_code
    # 上游 error.code 最具体，优先于 SDK 归纳的 reason。
    assert rfq_failure_code(S(status='failed',reason='EXECUTION_FAILED',error=S(code='MAKER_DECLINED')))=='maker_declined'
    assert rfq_failure_code(S(status='failed',reason=None,error=S(code='EXPIRED_RFQ')))=='expired_rfq'
    # 没有 error.code 时退回 reason；再没有才按状态兜底。
    assert rfq_failure_code(S(status='failed',reason='ACCEPTANCE_WINDOW_EXPIRED',error=None))=='acceptance_window_expired'
    assert rfq_failure_code(S(status='failed',reason='EXECUTION_FAILED',error=None))=='execution_failed'
    assert rfq_failure_code(S(status='FAILED',reason=None,error=None))=='rfq_failed'
    assert rfq_failure_code(S(status='executing',reason=None,error=None))=='accept_failed'
    # SDK 枚举对象与上游未来新增的错误码都要能原样保留，不能吞掉。
    class EnumLike:
        value='BRAND_NEW_REASON'
    assert rfq_failure_code(S(status='failed',reason=None,error=S(code=EnumLike())))=='brand_new_reason'


def test_combo_error_text_explains_known_reasons_without_inventing_unknown_ones():
    assert '做市商' in combo_api.combo_error_text('maker_declined')
    assert '过期' in combo_api.combo_error_text('acceptance_window_expired')
    assert '组合腿' in combo_api.combo_error_text('contradictory_legs')
    assert combo_api.combo_error_text('some_future_upstream_code') is None
    assert combo_api.combo_error_text(None) is None
