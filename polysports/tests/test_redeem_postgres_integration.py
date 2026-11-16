"""隔离 PostgreSQL 中的赎回资格、幂等和未知结果验证；不发真实交易。"""
from decimal import Decimal
import threading
import uuid

from sqlalchemy import text

from backend import redeem
from backend.redeem_gateway import RedeemUnknown
from test_z_auth_integration import pg  # noqa: F401
from test_zzz_trading_integration import env, preview  # noqa: F401


class FakeRedeemGateway:
    calls = []
    balances = []
    mutex = threading.Lock()
    def __init__(self, settings, wallet):
        self.settings = settings
        self.wallet = wallet
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def preflight_approval(self, **kwargs):
        self.approval = kwargs
    def wallet_status(self):
        with self.mutex:
            return {'collateral_balance': self.balances.pop(0)}
    def redeem(self, **kwargs):
        with self.mutex:
            self.calls.append(kwargs)
        return '0x' + 'd' * 64, 'mock-relayer-id'


def _create_filled_order(pg, client, headers, body):
    payload = preview(client, headers, body)
    response = client.post('/api/v1/single-orders', json=payload,
                           headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert response.status_code == 201, response.text
    intent_id = response.json()['data']['intent']['id']
    with pg.begin() as conn:
        conn.execute(text("UPDATE trade_intents SET state='filled',matched_size=5 WHERE id=:id"),
                     {'id': uuid.UUID(intent_id)})
        asset_id = conn.execute(text("""
            SELECT o.asset_id FROM trade_intents i
            JOIN market_outcomes o ON o.id=i.outcome_id WHERE i.id=:id
        """), {'id': uuid.UUID(intent_id)}).scalar_one()
    return intent_id, asset_id


def test_single_redemption_is_eligibility_gated_and_idempotent(pg, monkeypatch):
    pg, client, headers, body = env
    intent_id, asset_id = _create_filled_order(pg, client, headers, body)
    monkeypatch.setattr(redeem, 'fetch_redeemable_single_positions',
                        lambda *_args, **_kwargs: {asset_id: {
                            'shares': Decimal('5'), 'negative_risk': False,
                            'condition_id': None, 'outcome_index': 0}})
    monkeypatch.setattr(redeem, 'fetch_redeemable_combo_positions',
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(redeem, 'RedeemGateway', FakeRedeemGateway)
    FakeRedeemGateway.calls = []
    FakeRedeemGateway.balances = ['10', '15']
    client.app.state.settings.redeem_execution_enabled = True

    eligible = client.post('/api/v1/redemptions/eligibility',
                           json={'intent_ids': [intent_id], 'combo_ids': []}, headers=headers)
    assert eligible.status_code == 200, eligible.text
    item = eligible.json()['data']['items'][0]
    assert item['eligible'] is True and item['shares'] == '5'
    assert item['expected_payout'] == '5'

    first = client.post(f'/api/v1/trade-intents/{intent_id}/redeem', json={},
                        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert first.status_code == 201, first.text
    data = first.json()['data']
    assert data['state'] == 'confirmed' and data['credited_amount'] == '5'
    assert data['collateral_balance'] == '15' and data['tx_hash'] == '0x' + 'd' * 64
    assert FakeRedeemGateway.calls[0]['condition_id'] is not None
    assert FakeRedeemGateway.calls[0]['position_id'] is None

    second = client.post(f'/api/v1/trade-intents/{intent_id}/redeem', json={},
                         headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert second.status_code == 200, second.text
    assert second.json()['data']['already_redeemed'] is True
    assert len(FakeRedeemGateway.calls) == 1
    with pg.connect() as conn:
        row = conn.execute(text("""
            SELECT state,tx_hash,payout,shares FROM redeem_requests
            WHERE target_type='single' AND intent_id=:id
        """), {'id': uuid.UUID(intent_id)}).one()
        assert row.state == 'confirmed' and row.tx_hash == '0x' + 'd' * 64
        assert row.payout == Decimal('5') and row.shares == Decimal('5')


def test_unknown_redemption_cannot_be_repeated(pg, monkeypatch):
    pg, client, headers, body = env
    intent_id, asset_id = _create_filled_order(pg, client, headers, body)
    monkeypatch.setattr(redeem, 'fetch_redeemable_single_positions',
                        lambda *_args, **_kwargs: {asset_id: {
                            'shares': Decimal('5'), 'negative_risk': False,
                            'condition_id': None, 'outcome_index': 0}})
    monkeypatch.setattr(redeem, 'fetch_redeemable_combo_positions',
                        lambda *_args, **_kwargs: {})

    class UnknownGateway(FakeRedeemGateway):
        def redeem(self, **kwargs):
            self.calls.append(kwargs)
            raise RedeemUnknown('0x' + 'e' * 64, 'unknown-relayer-id')
    UnknownGateway.calls = []
    UnknownGateway.balances = ['10']
    monkeypatch.setattr(redeem, 'RedeemGateway', UnknownGateway)
    client.app.state.settings.redeem_execution_enabled = True

    first = client.post(f'/api/v1/trade-intents/{intent_id}/redeem', json={},
                        headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert first.status_code == 202, first.text
    assert first.json()['data']['state'] == 'unknown'
    second = client.post(f'/api/v1/trade-intents/{intent_id}/redeem', json={},
                         headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert second.status_code == 409
    assert len(UnknownGateway.calls) == 1


def test_combo_redemption_uses_position_id(pg, monkeypatch):
    pg, client, headers, body = env
    combo_id = uuid.uuid4()
    wallet_id = uuid.UUID(body['wallet_id'])
    with pg.begin() as conn:
        conn.execute(text("""
            INSERT INTO combo_rfqs
                (id,wallet_id,wallet_address,client_request_id,fingerprint,direction,quantity,
                 state,combo_position_id)
            VALUES
                (:id,:wallet_id,:address,:request_id,:fingerprint,'BUY',1,'confirmed','9001')
        """), {'id': combo_id, 'wallet_id': wallet_id, 'address': '0x' + '1' * 40,
               'request_id': uuid.uuid4(), 'fingerprint': 'f' * 64})
    monkeypatch.setattr(redeem, 'fetch_redeemable_single_positions',
                        lambda *_args, **_kwargs: {})
    monkeypatch.setattr(redeem, 'fetch_redeemable_combo_positions',
                        lambda *_args, **_kwargs: {'9001': {'shares': Decimal('4')}})
    monkeypatch.setattr(redeem, 'RedeemGateway', FakeRedeemGateway)
    FakeRedeemGateway.calls = []
    FakeRedeemGateway.balances = ['2', '6']
    client.app.state.settings.redeem_execution_enabled = True

    eligible = client.post('/api/v1/redemptions/eligibility',
                           json={'intent_ids': [], 'combo_ids': [str(combo_id)]}, headers=headers)
    assert eligible.status_code == 200, eligible.text
    assert eligible.json()['data']['items'][0]['eligible'] is True
    result = client.post(f'/api/v1/combo-rfqs/{combo_id}/redeem', json={},
                         headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert result.status_code == 201, result.text
    assert FakeRedeemGateway.calls == [{'condition_id': None, 'position_id': '9001'}]
    assert result.json()['data']['credited_amount'] == '4'
