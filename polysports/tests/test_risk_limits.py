from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.risk_limits import check_buy_limit
from backend.trading_gateway import TradingError
from test_z_auth_integration import ORIGIN, log_in, pg  # noqa: F401


@pytest.mark.parametrize(('balance', 'allowed'), [('100', '6'), ('10', '1'), ('0.5', '0.5')])
def test_percent_floor_is_capped_at_available_balance(balance, allowed):
    settings = SimpleNamespace(order_limit_percent=Decimal('6'), order_limit_floor_usd=Decimal('1'))
    check_buy_limit(settings, Decimal(allowed), balance)
    with pytest.raises(TradingError, match='超过单笔买入上限'):
        check_buy_limit(settings, Decimal(allowed) + Decimal('0.000001'), balance)


def test_enabled_limit_fails_closed_for_missing_balance():
    settings = SimpleNamespace(order_limit_percent=Decimal('6'), order_limit_floor_usd=Decimal('1'))
    with pytest.raises(TradingError, match='无法读取可用余额'):
        check_buy_limit(settings, Decimal('1'), None)


def test_single_and_combo_reservations_share_atomic_wallet_budget(pg):  # noqa: F811
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    from sqlalchemy.orm import Session

    from backend.risk_limits import limit_snapshot, release_buy_budget, reserve_buy_budget

    wallet = uuid4()
    orders = [uuid4(), uuid4()]
    settings = SimpleNamespace(
        order_limit_percent=None, order_limit_floor_usd=Decimal('1'), max_daily_usd=Decimal('10')
    )
    start = Barrier(2)

    def reserve(order):
        start.wait(timeout=5)
        try:
            with Session(pg) as db, db.begin():
                reserve_buy_budget(db, settings, wallet, order, Decimal('6'), None)
        except TradingError as exc:
            return exc.code
        return 'reserved'

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(reserve, orders)) == ['daily_limit_exceeded', 'reserved']
        with Session(pg) as db:
            assert limit_snapshot(db, settings, wallet)['remaining_daily_usd'] == Decimal('4')
    finally:
        with Session(pg) as db, db.begin():
            for order in orders:
                release_buy_budget(db, order)


def test_admin_can_update_trade_limits_without_restart(pg):  # noqa: F811
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with TestClient(create_app(), base_url=ORIGIN) as client:
        login = log_in(client)
        assert login.status_code == 200
        csrf = login.json()['data']['csrf_token']
        settings = {'order_limit_percent': '6', 'order_limit_floor_usd': '1', 'max_daily_usd': '20'}
        assert client.put('/api/v1/system/trade-limits', json=settings).status_code == 403
        saved = client.put(
            '/api/v1/system/trade-limits', json=settings, headers={'origin': ORIGIN, 'x-csrf-token': csrf}
        )
        assert saved.status_code == 200
        assert client.get('/api/v1/system/trade-limits').json()['data'] == settings
