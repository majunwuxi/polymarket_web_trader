import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import create_app
from backend.models import Game, League, Team
from test_z_auth_integration import ORIGIN, log_in, pg  # noqa: F401


def test_football_display_filters_the_list_before_pagination(pg):  # noqa: F811
    now = datetime.now(UTC)
    with Session(pg) as db, db.begin():
        for offset, league_id in enumerate(['306', '1234', '102540']):
            league = League(id=uuid.uuid4(), provider='polymarket', sport='football', external_id=league_id, name=league_id)
            home = Team(id=uuid.uuid4(), sport='football', source_name='Home '+league_id)
            away = Team(id=uuid.uuid4(), sport='football', source_name='Away '+league_id)
            db.add_all([league, home, away])
            db.flush()
            db.add(Game(
                provider='polymarket', external_id=league_id, sport='football', league_id=league.id,
                starts_at=now+timedelta(hours=offset+1), status='scheduled', market_available=True,
                home_team_id=home.id, away_team_id=away.id,
            ))
    with TestClient(create_app(), base_url=ORIGIN) as client:
        headers = {'origin': ORIGIN, 'x-csrf-token': log_in(client).json()['data']['csrf_token']}
        settings = '/api/v1/system/football-display'
        upcoming = '/api/v1/games?sport=football&limit=1'
        assert client.get(upcoming).json()['data']['items'][0]['external_id'] == '306'
        assert client.put(settings, json={'competition_ids':['1234']}).status_code == 403
        assert client.put(settings, json={'competition_ids':['999']}, headers=headers).status_code == 422
        assert client.put(settings, json={'competition_ids':['1234','102544']}, headers=headers).status_code == 200
        result = client.get(upcoming).json()['data']
        assert result['items'][0]['external_id'] == '1234'
        assert result['next_cursor']
        assert client.get(upcoming+'&cursor='+result['next_cursor']).json()['data']['items'][0]['external_id'] == '102540'
        assert client.put(settings, json={'competition_ids':[]}, headers=headers).status_code == 200
        assert client.get(upcoming).json()['data']['items'] == []
        assert client.put(settings, json={'competition_ids':None}, headers=headers).status_code == 200
        assert client.get(upcoming).json()['data']['items'][0]['external_id'] == '306'
