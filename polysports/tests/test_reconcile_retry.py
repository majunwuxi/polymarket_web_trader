from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as S
import uuid

from backend import combo_reconcile, result_binding
from backend.combo_reconcile import MAX_COMBO_PENDING_CHECKS, _confirm_from_chain, apply_combo
from backend.scheduler import MAX_CONSECUTIVE_FAILURES, finish_attempt, retry_or_fail


def attempt():
    return S(result='running', finished_at=None, error_code=None, error_summary=None)


class Db:
    def __init__(self, row):
        self.row = row

    def scalar(self, query):
        return self.row


def job(failures=0):
    return S(id=uuid.uuid4(), attempt_count=1, lease_token=uuid.uuid4(), lease_owner='worker',
             lease_until=None, failure_count=failures, status='leased', due_at=None,
             last_error_code=None, last_error_summary=None, updated_at=None)


def test_retry_stops_after_consecutive_failures():
    now = datetime.now(timezone.utc)
    row = job()
    retry_or_fail(Db(attempt()), row, now, 'internal_error', timedelta(minutes=2))
    assert row.status == 'pending'
    assert row.failure_count == 1
    assert row.last_error_code == 'internal_error'
    assert row.due_at == now + timedelta(minutes=2)

    row.failure_count = MAX_CONSECUTIVE_FAILURES - 1
    row.status = 'leased'
    retry_or_fail(Db(attempt()), row, now, 'internal_error', timedelta(minutes=2))
    assert row.status == 'failed'
    assert row.failure_count == MAX_CONSECUTIVE_FAILURES
    assert row.last_error_code == 'retry_exhausted'
    assert '停止自动重试' in row.last_error_summary


def test_success_resets_failure_streak():
    row = job(failures=4)
    finish_attempt(Db(attempt()), row, datetime.now(timezone.utc), 'succeeded')
    assert row.failure_count == 0
    assert row.status == 'leased'


def test_nonterminal_success_can_preserve_pending_streak():
    row = job(failures=4)
    finish_attempt(Db(attempt()), row, datetime.now(timezone.utc), 'succeeded', reset_failures=False)
    assert row.failure_count == 4


def test_combo_watchdog_confirms_pending_rows_and_closes_job(monkeypatch):
    class Db:
        def __init__(self, rows, job_row):
            self.rows = rows
            self.job_row = job_row
            self.first = True

        def scalars(self, query):
            if self.first:
                self.first = False
                return self.rows
            return []

        def scalar(self, query):
            return self.job_row

    now = datetime.now(timezone.utc)
    rows = [S(id=uuid.uuid4(), updated_at=now), S(id=uuid.uuid4(), updated_at=now)]
    job_row = job();job_row.status='pending'
    db = Db(rows, job_row)
    monkeypatch.setattr(combo_reconcile, '_confirm_from_chain', lambda db, row, job, settings, now: row is rows[0])
    counts = combo_reconcile.run_combo_watchdog(db, S(polygon_rpc_url=object()), now)
    assert counts == {'scanned': 2, 'confirmed': 1, 'closed': 0}
    assert job_row.status == 'done' and job_row.failure_count == 0


def test_combo_failure_cap_checks_chain_before_unknown(monkeypatch):
    class Db:
        def __init__(self, combo, now):
            self.values = iter([combo, now])

        def scalar(self, query):
            return next(self.values)

    now = datetime.now(timezone.utc)
    combo = S(id=uuid.uuid4(), state='executing', direction='BUY')
    row = job(failures=MAX_CONSECUTIVE_FAILURES - 1)
    row.combo_rfq_id = combo.id
    called = []
    monkeypatch.setattr(combo_reconcile, '_confirm_from_chain', lambda *args: (called.append(1) or True))
    combo_reconcile.apply_combo(Db(combo, now), row, None, error='internal_error', settings=S(polygon_rpc_url=object()))
    assert called == [1]
    assert row.status == 'leased'


def test_combo_rfq_404_confirms_from_onchain_balance(monkeypatch):
    class Db:
        def __init__(self, fill, attempt):
            self.values = iter([fill, fill, attempt])
            self.added = []

        def scalar(self, query):
            return next(self.values)

        def scalars(self, query):
            return [uuid.uuid4(), uuid.uuid4()]

        def add(self, value):
            self.added.append(value)

    now = datetime.now(timezone.utc)
    row = S(id=uuid.uuid4(), state='executing', direction='BUY', combo_position_id='123456',
            wallet_address='0x' + '1' * 40, error_code=None, updated_at=None, tx_hash=None)
    fill = S(state='pending', size=Decimal('2'), tx_hash=None, confirmed_at=None)
    job_row = job()
    job_row.combo_rfq_id = row.id
    db = Db(fill, attempt())
    bound = []
    monkeypatch.setattr(combo_reconcile, 'combo_position_balance', lambda *args: Decimal('2'))
    monkeypatch.setattr(result_binding, 'request_binding_for_accepted_order', lambda db, game_id: bound.append(game_id))
    assert _confirm_from_chain(db, row, job_row, S(polygon_rpc_url=object()), now)
    assert row.state == 'confirmed'
    assert fill.state == 'confirmed' and fill.confirmed_at == now
    assert job_row.status == 'done' and job_row.failure_count == 0
    assert db.added[-1].state == 'confirmed'
    assert len(bound) == 2


def test_combo_rfq_not_found_stops_without_retry():
    class SequenceDb:
        def __init__(self, *values):
            self.values = iter(values)
            self.added = []

        def scalar(self, query):
            return next(self.values)

        def add(self, value):
            self.added.append(value)

    now = datetime.now(timezone.utc)
    combo = S(id=uuid.uuid4(), state='executing', external_rfq_id='ext', quote_expires_at=None)
    row = job()
    row.combo_rfq_id = combo.id
    db = SequenceDb(combo, now, attempt())
    apply_combo(db, row, None, error='rfq_not_found')
    assert row.status == 'failed'
    assert row.last_error_code == 'rfq_not_found'
    assert combo.state == 'unknown' and combo.error_code == 'rfq_not_found'
    assert db.added and db.added[-1].state == 'unknown'


def test_combo_matched_status_stays_executing():
    class SequenceDb:
        def __init__(self, *values):
            self.values = iter(values)

        def scalar(self, query):
            return next(self.values)

    now = datetime.now(timezone.utc)
    combo = S(id=uuid.uuid4(), state='executing', external_rfq_id='ext', quote_expires_at=None)
    row = job()
    row.combo_rfq_id = combo.id
    apply_combo(SequenceDb(combo, now, attempt()), row, S(rfq_id='ext', status='MATCHED'))
    assert row.status == 'pending'
    assert combo.state == 'executing'


def test_combo_reconcile_stops_after_pending_limit():
    class SequenceDb:
        def __init__(self, *values):
            self.values = iter(values)
            self.added = []

        def scalar(self, query):
            return next(self.values)

        def add(self, value):
            self.added.append(value)

    now = datetime.now(timezone.utc)
    combo = S(id=uuid.uuid4(), state='executing', external_rfq_id='ext', quote_expires_at=None)
    row = job(failures=MAX_COMBO_PENDING_CHECKS - 1)
    row.combo_rfq_id = combo.id
    db = SequenceDb(combo, now, attempt())
    apply_combo(db, row, S(rfq_id='ext', status='EXECUTING'))
    assert row.status == 'failed'
    assert row.failure_count == MAX_COMBO_PENDING_CHECKS
    assert row.last_error_code == 'reconcile_stale'
    assert '停止自动重试' in row.last_error_summary
    assert combo.state == 'unknown' and combo.error_code == 'reconcile_stale'
    assert db.added and db.added[-1].state == 'unknown'
