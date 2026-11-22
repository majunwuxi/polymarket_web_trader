"""Daily retention cleanup for authentication and audit tables."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

SESSION_RETENTION_DAYS = 1
RATE_LIMIT_RETENTION_HOURS = 24
AUDIT_RETENTION_DAYS = 30


def cleanup_expired(db: Session) -> dict[str, int]:
    """Delete rows that can no longer affect authentication or audit history."""

    def count(result):
        return max(0, result.rowcount or 0)

    sessions = count(
        db.execute(
            text("""
        DELETE FROM admin_sessions
        WHERE expires_at < now() - make_interval(days => :days)
           OR (revoked_at IS NOT NULL AND revoked_at < now() - make_interval(days => :days))
    """),
            {'days': SESSION_RETENTION_DAYS},
        )
    )
    limits = count(
        db.execute(
            text("""
        DELETE FROM auth_rate_limits
        WHERE updated_at < now() - make_interval(hours => :hours)
          AND (blocked_until IS NULL OR blocked_until <= now())
    """),
            {'hours': RATE_LIMIT_RETENTION_HOURS},
        )
    )
    audit = count(
        db.execute(
            text("""
        DELETE FROM audit_events
        WHERE created_at < now() - make_interval(days => :days)
    """),
            {'days': AUDIT_RETENTION_DAYS},
        )
    )
    return {'admin_sessions': sessions, 'auth_rate_limits': limits, 'audit_events': audit}
