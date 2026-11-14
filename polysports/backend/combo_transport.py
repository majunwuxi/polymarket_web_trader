"""Combo SDK 发送边界；6G 完成前不得作为用户交易入口。"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
import re

from polymarket._internal.actions import combo_rfq


class AcceptanceAlreadySent(RuntimeError):
    """本次接受调用已尝试发送，外部结果需要按 RFQ ID 对账。"""


class _OneAcceptanceTransport:
    def __init__(self, delegate, path: str, expected=None):
        self._delegate = delegate
        self._path = path
        self._sent = False
        self._expected = expected

    def post_json(self, path, *args, **kwargs):
        if path != self._path:
            raise RuntimeError('不允许向其他地址发送 Combo 接受请求')
        if self._sent:
            raise AcceptanceAlreadySent('接受请求结果未知；禁止自动重发')
        if self._expected is not None:
            payload=kwargs.get('json'); order=payload.get('signed_order') if isinstance(payload,dict) else None
            if not isinstance(order,dict) or payload.get('quote_id')!=self._expected['quote_id']:
                raise ValueError('组合签名订单身份不一致，未发送')
            for key,value in self._expected.items():
                if key=='quote_id':continue
                actual=order.get(key)
                if str(actual).lower()!=str(value).lower():raise ValueError('组合签名订单参数不一致，未发送')
            if not re.fullmatch(r'0x[0-9a-fA-F]+',str(order.get('signature',''))):raise ValueError('组合签名无效，未发送')
        # 必须在进入底层传输之前设置；异常不能证明请求未发出。
        self._sent = True
        return self._delegate.post_json(path, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def accept_once(client, quote):
    """使用固定版本 SDK 签名，但禁止其内部异常重发。调用者负责先持久化意图。"""
    from .combo_requester import RequesterClient
    if isinstance(client, RequesterClient):
        return client.accept_once(quote)
    rfq_id = quote.rfq_id
    combo_rfq._require_rfq_id(rfq_id)
    # SDK 的路径编码逻辑是协议的一部分，避免把 RFQ ID 直接拼接到 URL。
    path = f"{combo_rfq._REQUESTS_PATH}/{combo_rfq._encode_path_segment(rfq_id)}/accept"
    context=client._ctx
    if str(context.wallet_type)!='DEPOSIT_WALLET':raise ValueError('不支持的组合钱包类型')
    with localcontext() as decimal_context:
        decimal_context.prec=80
        maker=Decimal(quote.maker_amount)*Decimal(10**6); taker=Decimal(quote.taker_amount)*Decimal(10**6)
    if not maker.is_finite() or not taker.is_finite() or maker<=0 or taker<=0 or maker!=maker.to_integral_value() or taker!=taker.to_integral_value():raise ValueError('组合金额无法精确表示')
    direction=str(quote.direction).upper().split('.')[-1]
    if direction not in ('BUY','SELL'):raise ValueError('组合方向无效')
    expected={'quote_id':str(quote.quote_id),'maker':str(context.wallet),'signer':str(context.wallet),'tokenId':str(quote.position_id),'makerAmount':str(int(maker)),'takerAmount':str(int(taker)),'side':0 if direction=='BUY' else 1,'signatureType':3,'builder':str(quote.builder_code),'metadata':'0x'+'0'*64}
    ctx = replace(context, builder_gateway=_OneAcceptanceTransport(context.builder_gateway, path, expected))
    return combo_rfq.accept_combo_quote_sync(ctx, quote)
