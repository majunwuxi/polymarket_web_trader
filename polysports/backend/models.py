from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Integer, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
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
    __table_args__ = (Index('ix_sync_runs_provider_started','provider',text('started_at DESC')),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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
