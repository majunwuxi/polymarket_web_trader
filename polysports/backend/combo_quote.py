"""组合报价的可信字段检查；真实资金流程尚未对外启用。"""
from decimal import Decimal, InvalidOperation
from .trading_gateway import TradingError


def verify_quote(quote, *, rfq_id: str, direction: str, quantity: Decimal, now_ms: int):
    if quote is None or str(quote.rfq_id) != rfq_id or str(quote.direction).upper().split('.')[-1] != direction:
        raise TradingError('combo_quote_mismatch', '组合报价身份或方向不一致')
    if type(quote.expires_at) is not int or quote.expires_at <= now_ms:
        raise TradingError('combo_quote_expired', '组合报价已过期')
    try:
        price=Decimal(quote.blended_price)
        maker=Decimal(quote.maker_amount)
        taker=Decimal(quote.taker_amount)
        required=Decimal(quote.total_required)
        if any(not x.is_finite() or x <= 0 for x in (price,maker,taker,required)) or price >= 1:
            raise ValueError()
        if maker > required:raise TradingError('combo_quote_mismatch', '签名支出超过报价要求')
        if direction == 'BUY' and required > quantity:
            raise TradingError('combo_quote_mismatch', '报价超过确认的预算')
        if direction == 'SELL':
            net=Decimal(quote.net_receive)
            if not net.is_finite() or net <= 0 or required > quantity:
                raise TradingError('combo_quote_mismatch', '报价份额或实际收入无效')
    except TradingError:
        raise
    except (InvalidOperation, TypeError, ValueError):
        raise TradingError('combo_quote_mismatch', '组合报价金额无效')
