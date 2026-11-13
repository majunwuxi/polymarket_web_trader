from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import Integer, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, Time, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (CheckConstraint("singleton", name="ck_admin_singleton_true"), UniqueConstraint("singleton", name="uq_admin_singleton"), UniqueConstraint("username", name="uq_admin_username"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_session_token_hash"), Index("ix_admin_sessions_admin_expiry", "admin_id", "expires_at"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    csrf_secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (CheckConstraint("attempt_count >= 0", name="ck_auth_attempt_count"), Index("ix_auth_rate_limits_updated_at", "updated_at"))
    bucket_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_created_at", text("created_at DESC")), Index("ix_audit_events_entity", "entity_type", "entity_id", text("created_at DESC")))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    details: Mapped[dict] = mapped_column(__import__("sqlalchemy").dialects.postgresql.JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

class Team(Base):
    __tablename__ = 'teams'
    __table_args__ = (CheckConstraint("sport IN ('football','nba')", name='ck_teams_sport'), CheckConstraint("translation_status IN ('verified','missing') AND (translation_status != 'verified' OR (name_zh IS NOT NULL AND length(trim(name_zh)) > 0))", name='ck_teams_translation'))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_zh: Mapped[str | None] = mapped_column(Text)
    translation_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default='missing')
    logo_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TeamSourceId(Base):
    __tablename__ = 'team_source_ids'
    __table_args__ = (UniqueConstraint('provider', 'external_id', name='uq_team_source'), Index('ix_team_source_team', 'team_id'))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('teams.id', ondelete='RESTRICT'), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)


class TeamAlias(Base):
    __tablename__ = 'team_aliases'
    __table_args__ = (UniqueConstraint('team_id','normalized_alias','language',name='uq_team_alias'), Index('ix_team_alias_normalized','normalized_alias'))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('teams.id', ondelete='RESTRICT'), nullable=False)
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_alias: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))


class League(Base):
    __tablename__ = 'leagues'
    __table_args__ = (UniqueConstraint('provider','external_id',name='uq_league_source'), CheckConstraint("sport IN ('football','nba')", name='ck_leagues_sport'))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_zh: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SyncRun(Base):
    __tablename__ = 'sync_runs'
    __table_args__ = (Index('ix_sync_runs_provider_started','provider',text('started_at DESC')), Index('uq_sync_schedule_occurrence','schedule_id','scheduled_for',unique=True,postgresql_where=text('schedule_id IS NOT NULL')))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('sync_schedules.id', ondelete='RESTRICT'))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    items_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    items_upserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(Text)


class Game(Base):
    __tablename__ = 'games'
    __table_args__ = (UniqueConstraint('provider','external_id',name='uq_game_source'), CheckConstraint("sport IN ('football','nba')",name='ck_games_sport'), CheckConstraint('home_team_id <> away_team_id',name='ck_games_distinct_teams'), CheckConstraint('(home_score IS NULL OR home_score >= 0) AND (away_score IS NULL OR away_score >= 0)',name='ck_games_scores'), Index('ix_games_sport_starts','sport','starts_at','id'), Index('ix_games_status_starts','status','starts_at'))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    league_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('leagues.id', ondelete='RESTRICT'))
    home_team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('teams.id', ondelete='RESTRICT'), nullable=False)
    away_team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('teams.id', ondelete='RESTRICT'), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(100))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GameRevision(Base):
    __tablename__ = 'game_revisions'
    __table_args__ = (Index('ix_game_revisions_game_observed','game_id',text('observed_at DESC')),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey('games.id', ondelete='RESTRICT'), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    provider_status: Mapped[str | None] = mapped_column(String(100))
    old_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    old_status: Mapped[str | None] = mapped_column(String(24))
    new_status: Mapped[str | None] = mapped_column(String(24))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey('sync_runs.id', ondelete='RESTRICT'))
    change_reason: Mapped[str] = mapped_column(String(80), nullable=False)

class SyncSchedule(Base):
    __tablename__ = 'sync_schedules'
    __table_args__ = (UniqueConstraint('provider','sport','utc_time',name='uq_sync_schedule'),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32),nullable=False)
    sport: Mapped[str] = mapped_column(String(16),nullable=False)
    utc_time: Mapped[time] = mapped_column(Time,nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean,nullable=False,server_default=text('true'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())


class ScheduleRevision(Base):
    __tablename__ = 'schedule_revisions'
    __table_args__ = (CheckConstraint('id = 1',name='ck_schedule_revision_singleton'),)
    id: Mapped[int] = mapped_column(Integer,primary_key=True,default=1)
    revision: Mapped[int] = mapped_column(Integer,nullable=False,server_default='1')


class WatchedGame(Base):
    __tablename__ = 'watched_games'
    __table_args__ = (UniqueConstraint('game_id',name='uq_watched_game'),CheckConstraint("state IN ('active','completed','paused')",name='ck_watched_state'))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    game_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),ForeignKey('games.id',ondelete='RESTRICT'),nullable=False)
    state: Mapped[str] = mapped_column(String(20),nullable=False)
    watched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    first_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())


class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('dedupe_key',name='uq_jobs_dedupe'),CheckConstraint("kind IN ('sync','result_poll')",name='ck_jobs_kind'),CheckConstraint("status IN ('pending','leased','done','cancelled','failed')",name='ck_jobs_status'),CheckConstraint("(status = 'leased' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL AND lease_token IS NOT NULL) OR (status <> 'leased' AND lease_owner IS NULL AND lease_until IS NULL AND lease_token IS NULL)",name='ck_jobs_lease'),CheckConstraint("(kind = 'result_poll' AND watched_game_id IS NOT NULL AND sync_run_id IS NULL) OR (kind = 'sync' AND watched_game_id IS NULL AND sync_run_id IS NOT NULL)",name='ck_jobs_target'),Index('ix_jobs_pending_due','due_at','id',postgresql_where=text("status = 'pending'")),Index('ix_jobs_leased_until','lease_until','id',postgresql_where=text("status = 'leased'")),Index('ix_jobs_watched','watched_game_id'),Index('uq_jobs_sync_run','sync_run_id',unique=True,postgresql_where=text('sync_run_id IS NOT NULL')))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(24),nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255),nullable=False)
    watched_game_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),ForeignKey('watched_games.id',ondelete='RESTRICT'))
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),ForeignKey('sync_schedules.id',ondelete='RESTRICT'))
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),ForeignKey('sync_runs.id',ondelete='RESTRICT'))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    status: Mapped[str] = mapped_column(String(16),nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    attempt_count: Mapped[int] = mapped_column(Integer,nullable=False,server_default='0')
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now())


class JobRun(Base):
    __tablename__ = 'job_runs'
    __table_args__ = (UniqueConstraint('job_id','attempt_no',name='uq_job_attempt'),Index('ix_job_runs_job_started','job_id',text('started_at DESC')))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),ForeignKey('jobs.id',ondelete='RESTRICT'),nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer,nullable=False)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str] = mapped_column(String(20),nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_summary: Mapped[str | None] = mapped_column(Text)


class WorkerHeartbeat(Base):
    __tablename__ = 'worker_heartbeats'
    worker_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False)
    worker_version: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(16),nullable=False)
