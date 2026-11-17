from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as S
import uuid

from backend.combo_reconcile import MAX_COMBO_PENDING_CHECKS, apply_combo
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
