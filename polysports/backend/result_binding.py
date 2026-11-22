"""只为经过交易所确认的订单持久化待绑定记录。"""

import json
import logging
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .gamma import normal
from .models import Game, ResultBindingRequest, Team, TeamAlias
from .scheduler import db_now, watch_game
from .sports_sources import SourceError, fetch_schedule
from .team_directory import NBA_SHORT_NAMES, football_name_variants, nba_name_variants

log = logging.getLogger('polysports.result_binding')


def request_binding_for_accepted_order(db, game_id):
    game = db.get(Game, game_id)
    if not game or game.provider != 'polymarket':
        return
    if game.sport not in ('football', 'nba'):
        raise ValueError('unsupported sport')
    provider = 'api_football' if game.sport == 'football' else 'balldontlie'
    stmt = insert(ResultBindingRequest).values(id=uuid.uuid4(), game_id=game.id, provider=provider, state='pending')
    db.execute(stmt.on_conflict_do_nothing(index_elements=['game_id']))
    watch_game(db, game.id)


def verified_team_names(db, team):
    names = {team.source_name}
    if team.name_zh and team.translation_status == 'verified':
        names.add(team.name_zh)
    names.update(db.scalars(select(TeamAlias.alias).where(TeamAlias.team_id == team.id, TeamAlias.verified.is_(True))))
    return {value for value in names if isinstance(value, str) and value.strip()}


def _team_names(sport, name, verified_names=None):
    names = {normal(name)}
    if verified_names:
        names.update(normal(value) for value in verified_names if isinstance(value, str) and value.strip())
    if sport == 'football':
        names.update(football_name_variants(name))
    if sport == 'nba':
        full = next((value for short, value in NBA_SHORT_NAMES.items() if normal(short) == normal(name)), None)
        if full:
            names.add(normal(full))
        # Polymarket 与 balldontlie 的队名写法不同（Clippers / LA Clippers），
        # 用目录里的精确别名把两侧统一到同一球队，不做模糊匹配。
        names.update(nba_name_variants(name))
        if full:
            names.update(nba_name_variants(full))
    return names


def match_fixture_candidates(sport, starts_at, home_name, away_name, fixtures, *, home_aliases=None, away_aliases=None):
    """匹配结果仅作为候选，不能据此自动认定最终比分或市场结算。"""
    return match_fixture_candidates_detailed(
        sport, starts_at, home_name, away_name, fixtures, home_aliases=home_aliases, away_aliases=away_aliases
    )[0]


def match_fixture_candidates_detailed(
    sport, starts_at, home_name, away_name, fixtures, *, home_aliases=None, away_aliases=None
):
    """同时返回匹配结果与诊断信息；诊断只用于日志，不参与任何绑定决定。"""
    if sport not in ('football', 'nba'):
        raise ValueError('unsupported sport')
    provider = 'api_football' if sport == 'football' else 'balldontlie'
    home_names = _team_names(sport, home_name, home_aliases)
    away_names = _team_names(sport, away_name, away_aliases)
    matches = []
    seen = {}
    diagnostics = {'provider_rows': 0, 'time_window_rows': 0, 'home_matches': 0, 'away_matches': 0, 'reversed_order': 0}
    for fixture in fixtures:
        if fixture.sport != sport or fixture.provider != provider:
            continue
        diagnostics['provider_rows'] += 1
        if abs((fixture.starts_at - starts_at).total_seconds()) > 7200:
            continue
        diagnostics['time_window_rows'] += 1
        fixture_home, fixture_away = normal(fixture.home.name), normal(fixture.away.name)
        home_ok = fixture_home in home_names
        away_ok = fixture_away in away_names
        if home_ok:
            diagnostics['home_matches'] += 1
        if away_ok:
            diagnostics['away_matches'] += 1
        if not home_ok or not away_ok:
            # 记录主客顺序反向的样本，便于区分“队名不匹配”和“主客颠倒”。
            if not home_ok and not away_ok and fixture_home in away_names and fixture_away in home_names:
                diagnostics['reversed_order'] += 1
            continue
        identity = (fixture.starts_at, fixture_home, fixture_away)
        if fixture.external_id in seen:
            if seen[fixture.external_id] != identity:
                raise SourceError('conflicting_fixture')
            continue
        seen[fixture.external_id] = identity
        matches.append(fixture)
    diagnostics['home_names'] = sorted(home_names)
    diagnostics['away_names'] = sorted(away_names)
    diagnostics['sample'] = [
        {'home': f.home.name, 'away': f.away.name, 'starts_at': f.starts_at.isoformat()}
        for f in fixtures
        if f.sport == sport and f.provider == provider and abs((f.starts_at - starts_at).total_seconds()) <= 7200
    ][:5]
    return sorted(matches, key=lambda item: (item.starts_at, item.external_id)), diagnostics


def find_fixture_candidates(
    client, key, sport, starts_at, home_name, away_name, *, home_aliases=None, away_aliases=None, diagnostics=None
):
    """在包含比赛时间的 36 小时窗口查询外部数据，不使用推测结果。"""
    provider = 'api_football' if sport == 'football' else 'balldontlie' if sport == 'nba' else None
    if not provider:
        raise ValueError('unsupported sport')
    fixtures, _, _ = fetch_schedule(provider, key, starts_at - timedelta(hours=18), client)
    matches, info = match_fixture_candidates_detailed(
        sport, starts_at, home_name, away_name, fixtures, home_aliases=home_aliases, away_aliases=away_aliases
    )
    if diagnostics is not None:
        diagnostics.update(info)
    return matches


def unique_nba_fixture_id(sport, matches):
    """只允许唯一、明确的 NBA 数字来源编号自动绑定。"""
    if sport != 'nba' or len(matches) != 1:
        return None
    ident = matches[0].external_id
    return ident if isinstance(ident, str) and ident.isascii() and ident.isdigit() and len(ident) <= 255 else None


def process_pending_binding(engine, settings, client):
    """领取一个到期请求；行锁确保多个 worker 不会同时处理同一赛事。"""
    with Session(engine) as db, db.begin():
        now = db_now(db)
        request = db.scalar(
            select(ResultBindingRequest)
            .where(
                or_(
                    ResultBindingRequest.state == 'pending',
                    and_(ResultBindingRequest.state == 'needs_review', ResultBindingRequest.provider == 'balldontlie'),
                ),
                ResultBindingRequest.next_attempt_at <= now,
            )
            .order_by(ResultBindingRequest.next_attempt_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if request is None:
            return False
        game = db.get(Game, request.game_id)
        home = db.get(Team, game.home_team_id)
        away = db.get(Team, game.away_team_id)
        secret = settings.api_football_key if request.provider == 'api_football' else settings.balldontlie_key
        home_aliases = verified_team_names(db, home) if game.sport == 'football' else None
        away_aliases = verified_team_names(db, away) if game.sport == 'football' else None
        try:
            diagnostics = {}
            matches = find_fixture_candidates(
                client,
                secret.get_secret_value() if secret else '',
                game.sport,
                game.starts_at,
                home.source_name,
                away.source_name,
                home_aliases=home_aliases,
                away_aliases=away_aliases,
                diagnostics=diagnostics,
            )
            request.candidates = [
                {
                    'id': item.external_id,
                    'starts_at': item.starts_at.isoformat(),
                    'home': item.home.name,
                    'away': item.away.name,
                    'league': item.league_name,
                }
                for item in matches
            ]
            request.last_error_code = None if matches else 'no_matching_fixture'
            if not matches:
                # 只记录脱敏的匹配上下文，用于区分队名差异、主客相反或外部赛程缺失。
                log.info(
                    'binding_no_match sport=%s provider=%s starts_at=%s home=%s away=%s %s',
                    game.sport,
                    request.provider,
                    game.starts_at.isoformat(),
                    home.source_name,
                    away.source_name,
                    json.dumps(diagnostics, ensure_ascii=False, default=str),
                )
            if matches:
                request.state = 'needs_review'
                fixture_id = unique_nba_fixture_id(game.sport, matches)
                if fixture_id:
                    # 约束之外再按来源编号串行化，避免两个赛事并发绑定同一场比赛。
                    db.execute(
                        text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                        {'key': 'result-binding:' + request.provider + ':' + fixture_id},
                    )
                    occupied = db.scalar(
                        select(ResultBindingRequest.id).where(
                            ResultBindingRequest.provider == request.provider,
                            ResultBindingRequest.fixture_id == fixture_id,
                            ResultBindingRequest.id != request.id,
                        )
                    )
                    if occupied:
                        request.last_error_code = 'fixture_already_bound'
                    else:
                        try:
                            with db.begin_nested():
                                request.fixture_id = fixture_id
                                request.state = 'bound'
                                db.flush()
                        except IntegrityError:
                            request.fixture_id = None
                            request.state = 'needs_review'
                            request.last_error_code = 'fixture_already_bound'
        except SourceError as exc:
            request.last_error_code = exc.code
        request.updated_at = now
        request.next_attempt_at = now + timedelta(minutes=10)
        return True
