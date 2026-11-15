"""组合成交明细的确定金额口径：重建、落库状态流转与卡片盈亏。"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from backend.combo_fills import confirm_combo_fill, fill_payload, rebuilt_cost, void_combo_fill
from backend.pnl import combo_pnl


def _migration_module():
    path = Path(__file__).resolve().parents[1] / 'migrations' / 'versions' / 'b5d2e7c41a90_combo_fills.py'
    spec = importlib.util.spec_from_file_location('combo_fills_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**overrides):
    values = dict(id=uuid.uuid4(), direction='BUY', quote_id=uuid.uuid4(),
                  quote_price=Decimal('0.41'), quote_receive=Decimal('5.12'),
                  quote_required=Decimal('2.2'), tx_hash='0x' + 'a' * 64)
    values.update(overrides)
    return SimpleNamespace(**values)


def _db(fill=None):
    added = []

    class Db:
        def scalar(self, query):
            return fill

        def add(self, value):
            added.append(value)

    return Db(), added


def test_rebuilt_cost_uses_micro_usdc_precision_and_validates_required():
    assert rebuilt_cost(Decimal('0.41'), Decimal('5.12'), Decimal('2.2')) == Decimal('2.099200')
    assert rebuilt_cost(Decimal('0.333333'), Decimal('3'), Decimal('2')) == Decimal('0.999999')
    assert rebuilt_cost(Decimal('0.9'), Decimal('10'), Decimal('1')) is None  # 超过余额要求
    assert rebuilt_cost(Decimal('1'), Decimal('3'), Decimal('10')) is None
    assert rebuilt_cost(Decimal('0.4'), Decimal('0'), Decimal('10')) is None
    assert rebuilt_cost(None, Decimal('3'), Decimal('10')) is None
    assert rebuilt_cost('nan', Decimal('3'), Decimal('10')) is None


def test_migration_rebuild_matches_application_formula():
    migration = _migration_module()
    cases = [(Decimal('0.41'), Decimal('5.12'), Decimal('2.2')),
             (Decimal('0.333333'), Decimal('3'), Decimal('2')),
             (Decimal('0.9'), Decimal('10'), Decimal('1')),
             (Decimal('1'), Decimal('3'), Decimal('10'))]
    for price, shares, required in cases:
        assert migration._rebuilt_cost(price, shares, required) == rebuilt_cost(price, shares, required)


def _fill(**overrides):
    values = dict(cost=Decimal('2.0992'), size=Decimal('5.12'), source='quote_rebuilt',
                  state='confirmed', tx_hash='0x' + 'a' * 64, confirmed_at=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def test_combo_pnl_confirmed_fill_has_no_estimate_prefix():
    loss = combo_pnl(direction='BUY', by_score=False,
                     quote={'total_required': '2.2', 'net_receive': '5.12', 'price': '0.41'},
                     fill=fill_payload(_fill()))
    assert loss['basis'] == 'combo_fill' and loss['estimated'] is False
    assert loss['label'] == '亏损 2.0992 pUSD' and '约' not in loss['label']
    assert loss['tone'] == 'loss' and loss['cost'] == '2.0992' and loss['payout'] == '0'
    assert '重建的成交金额' in loss['detail']

    win = combo_pnl(direction='BUY', by_score=True,
                    fill=fill_payload(_fill(cost=Decimal('1.5'), source='signed_order')))
    assert win['label'] == '盈利 3.62 pUSD' and '约' not in win['label']
    assert win['tone'] == 'profit' and win['basis'] == 'combo_fill'
    assert '签名接受订单' in win['detail']

    flat = combo_pnl(direction='BUY', by_score=True,
                     fill=fill_payload(_fill(cost=Decimal('5.12'))))
    assert flat['tone'] == 'flat' and flat['label'] == '盈亏持平'


def test_combo_pnl_without_confirmed_fill_stays_estimated():
    pending = combo_pnl(direction='BUY', by_score=False,
                        quote={'total_required': '2.2', 'net_receive': '5.12', 'price': '0.41'},
                        fill=fill_payload(_fill(state='pending')))
    assert pending['estimated'] is True and pending['basis'] == 'quote_estimate'
    assert pending['label'] == '约亏损 2.2 pUSD'
    assert '尚未落库' in pending['detail']
    assert combo_pnl(direction='BUY', by_score=None, fill=fill_payload(_fill()))['tone'] == 'pending'
    assert combo_pnl(direction='SELL', by_score=True, fill=fill_payload(_fill()))['tone'] == 'na'


def test_confirm_marks_pending_fill_and_backfills_tx_hash():
    fill = _fill(state='pending', tx_hash=None)
    db, added = _db(fill)
    now = datetime.now(timezone.utc)
    row = _row(tx_hash='0x' + 'b' * 64)
    assert confirm_combo_fill(db, row, now) is fill
    assert fill.state == 'confirmed' and fill.tx_hash == row.tx_hash
    assert fill.confirmed_at == now and added == []
    assert fill_payload(fill)['state'] == 'confirmed'
    assert fill_payload(fill)['confirmed_at'] == now.isoformat()


def test_confirm_rebuilds_missing_fill_from_signed_quote():
    db, added = _db(None)
    fill = confirm_combo_fill(db, _row(), 'now')
    assert fill is not None and len(added) == 1 and added[0] is fill
    assert fill.cost == Decimal('2.099200') and fill.size == Decimal('5.12')
    assert fill.source == 'quote_rebuilt' and fill.state == 'confirmed'
    assert fill.tx_hash == '0x' + 'a' * 64


def test_confirm_never_invents_fill_for_sell_or_invalid_quote():
    db, added = _db(None)
    assert confirm_combo_fill(db, _row(direction='SELL'), 'now') is None and added == []
    assert confirm_combo_fill(db, _row(quote_price=None), 'now') is None and added == []
    assert confirm_combo_fill(db, _row(quote_required=Decimal('0.1')), 'now') is None and added == []


def test_void_only_discards_unconfirmed_fill():
    pending = _fill(state='pending')
    assert void_combo_fill(_db(pending)[0], _row()) is pending
    assert pending.state == 'void'
    confirmed = _fill(state='confirmed')
    assert void_combo_fill(_db(confirmed)[0], _row()) is confirmed
    assert confirmed.state == 'confirmed'
