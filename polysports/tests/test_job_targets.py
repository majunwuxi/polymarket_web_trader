from datetime import datetime, timezone
from types import SimpleNamespace as S
import uuid

from backend.models import ComboRFQ, Game, MarketOutcome, Team, TradeIntent, WatchedGame
from backend.scheduler_api import job_target


NOW = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)


class Db:
    def __init__(self, objects, legs=None):
        self.objects = objects
        self.legs = legs or []

    def get(self, model, ident):
        return self.objects.get((model, ident))

    def scalars(self, query):
        return self.legs


def base_objects():
    game_id = uuid.uuid4()
    game = S(id=game_id, home_team_id='home', away_team_id='away', starts_at=NOW,
             status='scheduled', sport='football')
    home = S(name_zh='主队', source_name='Home')
    away = S(name_zh='客队', source_name='Away')
    return game_id, {
        (Game, game_id): game,
        (Team, 'home'): home,
        (Team, 'away'): away,
    }


def test_result_poll_target_identifies_game():
    game_id, objects = base_objects()
    watch_id = uuid.uuid4()
    objects[(WatchedGame, watch_id)] = S(game_id=game_id)
    target = job_target(Db(objects), S(kind='result_poll', watched_game_id=watch_id))
    assert target['label'] == '主队 对 客队'
    assert target['game_id'] == str(game_id)
    assert target['game_status'] == 'scheduled'


def test_trade_and_combo_targets_identify_games():
    game_id, objects = base_objects()
    intent_id = uuid.uuid4()
    objects[(TradeIntent, intent_id)] = S(
        id=intent_id, game_id=game_id, outcome_id='out', state='unknown', side='BUY')
    objects[(MarketOutcome, 'out')] = S(label='主队')
    trade = job_target(Db(objects), S(kind='trade_reconcile', trade_intent_id=intent_id))
    assert trade['label'] == '主队 对 客队 · 主队'
    assert trade['trade_state'] == 'unknown'

    combo_id = uuid.uuid4()
    objects[(ComboRFQ, combo_id)] = S(id=combo_id, state='executing')
    legs = [S(game_id=game_id), S(game_id=game_id)]
    combo = job_target(Db(objects, legs), S(kind='combo_reconcile', combo_rfq_id=combo_id))
    assert combo['label'] == '主队 对 客队；主队 对 客队'
    assert len(combo['legs']) == 2
