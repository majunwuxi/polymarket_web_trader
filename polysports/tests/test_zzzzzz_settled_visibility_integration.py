"""交易记录隐藏规则的数据库集成测试；本机未提供隔离 PostgreSQL 时自动跳过。

纯 SQL 结构断言见 ``test_settled_visibility.py``，这里验证真实数据与分页下的行为。
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from test_z_auth_integration import pg  # noqa: F401
from test_zzz_trading_integration import env, preview  # noqa: F401
from backend.models import ComboLeg, ComboRFQ, Game, MarketLink, MarketOutcome, PolyMarket, ResultBindingRequest, TradeIntent

OLD = lambda: datetime.now(timezone.utc) - timedelta(hours=48)
RECENT = lambda: datetime.now(timezone.utc) - timedelta(hours=2)


def list_ids(client, path, hide):
    params = {'limit': 100}
    if hide:
        params['hide_settled'] = 'true'
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return [x['id'] for x in response.json()['data']['items']]


def set_binding(db, game, state):
    binding = db.scalar(select(ResultBindingRequest).where(ResultBindingRequest.game_id == game.id))
    if binding is None:
        db.add(ResultBindingRequest(id=uuid.uuid4(), game_id=game.id, provider='api_football',
                                    state=state, fixture_id='mock-' + uuid.uuid4().hex,
                                    next_attempt_at=datetime.now(timezone.utc)))
    else:
        binding.state = state


def test_single_order_hidden_only_when_old_and_settled(env):
    engine, client, headers, body = env
    submit = preview(client, headers, body)
    result = client.post('/api/v1/single-orders', json=submit,
                         headers={**headers, 'Idempotency-Key': str(uuid.uuid4())})
    assert result.status_code == 201, result.text
    ident = result.json()['data']['intent']['id']
    assert ident in list_ids(client, '/api/v1/trade-intents', False)

    with Session(engine) as db, db.begin():
        intent = db.get(TradeIntent, uuid.UUID(ident))
        game = db.get(Game, intent.game_id)
        game.starts_at = OLD()
        game.status = 'finished'
        game.home_score = 100
        game.away_score = 90
        set_binding(db, game, 'bound')
    # 老比赛 + 已结算：交易记录不显示，普通列表仍可见。
    assert ident not in list_ids(client, '/api/v1/trade-intents', True)
    assert ident in list_ids(client, '/api/v1/trade-intents', False)

    with Session(engine) as db, db.begin():
        intent = db.get(TradeIntent, uuid.UUID(ident))
        game = db.get(Game, intent.game_id)
        set_binding(db, game, 'pending')
    # 老比赛但未结算：保留。
    assert ident in list_ids(client, '/api/v1/trade-intents', True)

    with Session(engine) as db, db.begin():
        intent = db.get(TradeIntent, uuid.UUID(ident))
        game = db.get(Game, intent.game_id)
        game.starts_at = RECENT()
        set_binding(db, game, 'bound')
    # 已结算但开赛未满 24 小时：保留。
    assert ident in list_ids(client, '/api/v1/trade-intents', True)


def test_combo_hidden_only_when_last_leg_old_and_any_leg_settled(env):
    engine, client, headers, body = env
    link_id = uuid.UUID(body['market_link_id'])
    outcome_id = uuid.UUID(body['outcome_id'])
    wallet_id = uuid.UUID(body['wallet_id'])
    rfq_id = uuid.uuid4()
    late_game_id = uuid.uuid4()
    late_market_id = uuid.uuid4()
    late_link_id = uuid.uuid4()
    late_outcome_id = uuid.uuid4()
    with Session(engine) as db, db.begin():
        link = db.get(MarketLink, link_id)
        market = db.get(PolyMarket, link.market_id)
        first_game = db.get(Game, link.game_id)
        first_game.starts_at = OLD()
        late_game = Game(id=late_game_id, provider=first_game.provider,
                         external_id='mock-' + uuid.uuid4().hex, sport=first_game.sport,
                         home_team_id=first_game.home_team_id, away_team_id=first_game.away_team_id,
                         starts_at=RECENT(), status='scheduled')
        late_market = PolyMarket(id=late_market_id, gamma_market_id='mock-gamma-' + uuid.uuid4().hex,
                                 poly_event_id=market.poly_event_id,
                                 condition_id='0x' + uuid.uuid4().hex + uuid.uuid4().hex,
                                 question='模拟第二市场', accepting_orders=True,
                                 fingerprint=market.fingerprint, last_verified_at=market.last_verified_at)
        late_outcome = MarketOutcome(id=late_outcome_id, market_id=late_market_id, outcome_index=0,
                                     label='模拟第二选项', asset_id=str(uuid.uuid4().int)[:30],
                                     asset_kind='ctf_token')
        late_link = MarketLink(id=late_link_id, game_id=late_game_id, market_id=late_market_id,
                               state='confirmed', match_method='manual', evidence=link.evidence)
        db.add_all([late_game, late_market])
        # flush 的插入顺序只按 relationship() 排序，纯列级外键不参与（同批内按名称序）：
        # 必须先落 games/poly_markets，再插 market_outcomes 与 market_links。
        db.flush()
        db.add_all([late_outcome, late_link])
        db.flush()
        db.add(ComboRFQ(id=rfq_id, wallet_id=wallet_id, wallet_address='0x' + '1' * 40,
                        client_request_id=uuid.uuid4(), fingerprint=uuid.uuid4().hex,
                        direction='BUY', quantity=Decimal('10'), state='confirmed',
                        external_rfq_id='mock-rfq-' + uuid.uuid4().hex))
        db.flush()
        db.add_all([
            ComboLeg(id=uuid.uuid4(), rfq_id=rfq_id, game_id=first_game.id,
                     market_link_id=link_id, outcome_id=outcome_id, position_id='1001'),
            ComboLeg(id=uuid.uuid4(), rfq_id=rfq_id, game_id=late_game_id,
                     market_link_id=late_link_id, outcome_id=late_outcome_id, position_id='1002'),
        ])
    try:
        ident = str(rfq_id)
        assert ident in list_ids(client, '/api/v1/combo-rfqs', False)
        # 最晚一场比赛还没超过 24 小时：即使已停单也必须保留。
        with Session(engine) as db, db.begin():
            link = db.get(MarketLink, link_id)
            db.get(PolyMarket, link.market_id).accepting_orders = False
        assert ident in list_ids(client, '/api/v1/combo-rfqs', True)

        # 所有腿都开赛超过 24 小时 + 市场已停单：隐藏。
        with Session(engine) as db, db.begin():
            db.get(Game, late_game_id).starts_at = OLD()
        assert ident not in list_ids(client, '/api/v1/combo-rfqs', True)
        assert ident in list_ids(client, '/api/v1/combo-rfqs', False)

        # 未结算的老组合仍保留。
        with Session(engine) as db, db.begin():
            link = db.get(MarketLink, link_id)
            db.get(PolyMarket, link.market_id).accepting_orders = True
        assert ident in list_ids(client, '/api/v1/combo-rfqs', True)
    finally:
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM combo_legs WHERE rfq_id=:id'), {'id': str(rfq_id)})
            conn.execute(text('DELETE FROM combo_rfqs WHERE id=:id'), {'id': str(rfq_id)})
            conn.execute(text('DELETE FROM market_links WHERE id=:id'), {'id': str(late_link_id)})
            conn.execute(text('DELETE FROM market_outcomes WHERE id=:id'), {'id': str(late_outcome_id)})
            conn.execute(text('DELETE FROM poly_markets WHERE id=:id'), {'id': str(late_market_id)})
            conn.execute(text('DELETE FROM games WHERE id=:id'), {'id': str(late_game_id)})
