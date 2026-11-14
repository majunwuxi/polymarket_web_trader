"""个人 Combo Requester API。测试不得连接生产或发送真实订单。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal, localcontext

import httpx
from polymarket import RequestRejectedError
from polymarket._internal.actions import combo_rfq

from .combo_transport import _OneAcceptanceTransport

BASE_URL = 'https://combos-rfq-gateway-requester-api.polymarket.com'
REQUESTS_PATH = '/v1/requester/rfq/requests'
ZERO_BUILDER = '0x' + '0' * 64


class RequesterTransport:
    def __init__(self, context, *, transport=None):
        self.context = context
        self.http = httpx.Client(base_url=BASE_URL, transport=transport, timeout=combo_rfq._HELD_REQUEST_TIMEOUT, follow_redirects=False)

    def close(self):
        self.http.close()

    def request(self, method, path, body=None):
        if method not in ('POST', 'GET') or not path.startswith(REQUESTS_PATH):
            raise ValueError('组合请求路径无效')
        payload = json.dumps(body, separators=(',', ':'), ensure_ascii=False) if body is not None else ''
        timestamp = str(int(time.time()))
        credentials = self.context.credentials
        secret = base64.urlsafe_b64decode(credentials.secret)
        message = (timestamp + method + path + payload).encode('utf-8')
        signature = base64.urlsafe_b64encode(hmac.new(secret, message, hashlib.sha256).digest()).decode('ascii')
        headers = {'POLY_ADDRESS': str(self.context.signer.address), 'POLY_API_KEY': credentials.key,
                   'POLY_PASSPHRASE': credentials.passphrase, 'POLY_TIMESTAMP': timestamp,
                   'POLY_SIGNATURE': signature, 'Content-Type': 'application/json'}
        response = self.http.request(method, path, content=payload.encode('utf-8') if body is not None else None, headers=headers)
        if response.status_code != 200:
            raise RequestRejectedError('组合接口请求失败', status=response.status_code)
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('组合接口响应无效')
        return result

    def post_json(self, path, *, json, timeout=None):
        return self.request('POST', path, json)

    def get_json(self, path):
        return self.request('GET', path)


class RequesterClient:
    def __init__(self, sdk_client, *, transport=None):
        self.sdk_client = sdk_client
        self._ctx = sdk_client._ctx
        self.transport = RequesterTransport(self._ctx, transport=transport)

    def close(self):
        self.transport.close()
        self.sdk_client.close()

    def request_combo_quote(self, **kwargs):
        combo_rfq.assert_combos_supported(self._ctx)
        _, body = combo_rfq.build_combo_quote_request_body(
            self._ctx, leg_position_ids=kwargs['leg_position_ids'], direction=kwargs['direction'],
            amount=kwargs.get('amount'), size=kwargs.get('size'), side=kwargs.get('side', 'YES'))
        result = self.transport.post_json(REQUESTS_PATH, json=body)
        if result.get('builder_code', ZERO_BUILDER) != ZERO_BUILDER:
            raise ValueError('个人组合报价包含不允许的 Builder 归属')
        result['builder_code'] = ZERO_BUILDER
        return combo_rfq._parse_combo_quote_result(result, request=body)

    def fetch_rfq_status(self, *, rfq_id):
        combo_rfq._require_rfq_id(rfq_id)
        path = f'{REQUESTS_PATH}/{combo_rfq._encode_path_segment(rfq_id)}'
        return combo_rfq._parse_rfq_status(self.transport.get_json(path), expected_rfq_id=rfq_id)

    def accept_once(self, quote):
        combo_rfq._require_rfq_id(quote.rfq_id)
        if str(self._ctx.wallet_type) != 'DEPOSIT_WALLET' or quote.builder_code != ZERO_BUILDER:
            raise ValueError('组合订单钱包或 Builder 归属无效')
        with localcontext() as context:
            context.prec = 80
            maker = Decimal(quote.maker_amount) * Decimal(10**6)
            taker = Decimal(quote.taker_amount) * Decimal(10**6)
        if not maker.is_finite() or not taker.is_finite() or maker <= 0 or taker <= 0 or maker != maker.to_integral_value() or taker != taker.to_integral_value():
            raise ValueError('组合金额无法精确表示')
        direction = str(quote.direction).upper().split('.')[-1]
        if direction not in ('BUY', 'SELL'):
            raise ValueError('组合方向无效')
        expected = {'quote_id': str(quote.quote_id), 'maker': str(self._ctx.wallet), 'signer': str(self._ctx.wallet),
                    'tokenId': str(quote.position_id), 'makerAmount': str(int(maker)), 'takerAmount': str(int(taker)),
                    'side': 0 if direction == 'BUY' else 1, 'signatureType': 3, 'builder': ZERO_BUILDER, 'metadata': ZERO_BUILDER}
        body = combo_rfq._build_accept_request_body(self._ctx, quote)
        path = f'{REQUESTS_PATH}/{combo_rfq._encode_path_segment(quote.rfq_id)}/accept'
        once = _OneAcceptanceTransport(self.transport, path, expected)
        result = once.post_json(path, json=body)
        status = combo_rfq._parse_rfq_status(result, expected_rfq_id=quote.rfq_id)
        return combo_rfq._to_acceptance(status, taker_order_hash=status.taker_order_hash)
