"""外部赛果候选的查询、汇总和管理员确认。"""

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from . import auth
from .models import Game, League, ResultBindingRequest, Team
from .scheduler import db_now, watch_game

router = APIRouter(prefix='/api/v1')


def binding_data(item):
    return {
        'state': item.state,
        'provider': item.provider,
        'fixture_id': item.fixture_id,
        'candidates': item.candidates,
        'error_code': item.last_error_code,
    }


def binding_summary(engine, sport: str, limit: int):
    with Session(engine) as db:
        grouped = dict(
            db.execute(
                select(ResultBindingRequest.state, func.count())
                .join(Game, Game.id == ResultBindingRequest.game_id)
                .where(Game.sport == sport, Game.provider == 'polymarket')
                .group_by(ResultBindingRequest.state)
            ).all()
        )
        ready = (
            db.scalar(
                select(func.count())
                .select_from(ResultBindingRequest)
                .join(Game, Game.id == ResultBindingRequest.game_id)
                .where(
                    Game.sport == sport,
                    Game.provider == 'polymarket',
                    ResultBindingRequest.state == 'needs_review',
                    func.jsonb_array_length(ResultBindingRequest.candidates) > 0,
                )
            )
            or 0
        )
        home_team, away_team = aliased(Team), aliased(Team)
        rows = db.execute(
            select(ResultBindingRequest, Game, home_team, away_team, League)
            .join(Game, Game.id == ResultBindingRequest.game_id)
            .join(home_team, home_team.id == Game.home_team_id)
            .join(away_team, away_team.id == Game.away_team_id)
            .outerjoin(League, League.id == Game.league_id)
            .where(Game.sport == sport, Game.provider == 'polymarket', ResultBindingRequest.state == 'needs_review')
            .order_by(Game.starts_at, Game.id)
            .limit(limit + 1)
        ).all()
        more = len(rows) > limit
        items = []
        for binding, game, home, away, league in rows[:limit]:
            candidates = binding.candidates if isinstance(binding.candidates, list) else []
            items.append(
                {
                    'game_id': str(game.id),
                    'external_id': game.external_id,
                    'starts_at': game.starts_at.isoformat(),
                    'game_status': game.status,
                    'home': home.name_zh or home.source_name,
                    'away': away.name_zh or away.source_name,
                    'league': (league.name_zh or league.name) if league else None,
                    'state': binding.state,
                    'provider': binding.provider,
                    'fixture_id': binding.fixture_id,
                    'candidates': candidates,
                    'error_code': binding.last_error_code,
                    'updated_at': binding.updated_at.isoformat(),
                    'next_attempt_at': binding.next_attempt_at.isoformat(),
                    'can_quick_confirm': bool(candidates),
                }
            )
        bound = grouped.get('bound', 0)
        pending = grouped.get('pending', 0)
        needs = grouped.get('needs_review', 0)
        return {
            'sport': sport,
            'as_of': db_now(db).isoformat(),
            'totals': {
                'total': sum(grouped.values()),
                'bound': bound,
                'pending': pending,
                'needs_review': needs,
                'unbound': pending + needs,
                'ready_to_confirm': ready,
            },
            'items': items,
            'has_more': more,
        }


@router.get('/result-bindings/summary')
def get_binding_summary(
    request: Request, sport: Literal['football', 'nba'] = 'football', limit: int = Query(20, ge=1, le=20)
):
    return {'data': binding_summary(request.app.state.engine, sport, limit), 'request_id': request.state.request_id}


@router.get('/games/{game_id}/result-binding')
def get_binding(request: Request, game_id: uuid.UUID):
    with Session(request.app.state.engine) as db:
        item = db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id == game_id))
        return {'data': binding_data(item) if item else None, 'request_id': request.state.request_id}


class ConfirmBinding(BaseModel):
    fixture_id: str = Field(min_length=1, max_length=255, pattern=r'^[0-9]+$')


@router.post('/games/{game_id}/result-binding/confirm')
def confirm_binding(request: Request, game_id: uuid.UUID, body: ConfirmBinding):
    try:
        with Session(request.app.state.engine) as db, db.begin():
            item = db.scalar(
                select(ResultBindingRequest).where(ResultBindingRequest.game_id == game_id).with_for_update()
            )
            if not item:
                return auth.error(request, 404, 'NOT_FOUND', '赛事绑定请求不存在')
            if item.state == 'bound':
                if item.fixture_id != body.fixture_id:
                    return auth.error(request, 409, 'BINDING_CONFLICT', '赛事已绑定其他结果来源')
            elif item.state != 'needs_review' or not any(x.get('id') == body.fixture_id for x in item.candidates):
                return auth.error(request, 409, 'BINDING_INVALID', '候选已变化或不存在，请重新核对')
            else:
                item.fixture_id = body.fixture_id
                item.state = 'bound'
                item.updated_at = db_now(db)
                watch_game(db, game_id)
                db.flush()
            return {'data': binding_data(item), 'request_id': request.state.request_id}
    except IntegrityError:
        return auth.error(request, 409, 'BINDING_CONFLICT', '此外部赛事已绑定其他比赛')
