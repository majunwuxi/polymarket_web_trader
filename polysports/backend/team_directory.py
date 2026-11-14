"""仅用于显示的用户维护球队资料；不参与赛事或交易身份验证。"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import unicodedata

# Polymarket NBA 比赛使用球队简称；仅映射到用户提供目录中的明确全名。
NBA_SHORT_NAMES = {
    'Hawks':'Atlanta Hawks', 'Celtics':'Boston Celtics', 'Nets':'Brooklyn Nets',
    'Hornets':'Charlotte Hornets', 'Bulls':'Chicago Bulls', 'Cavaliers':'Cleveland Cavaliers',
    'Mavericks':'Dallas Mavericks', 'Nuggets':'Denver Nuggets', 'Pistons':'Detroit Pistons',
    'Warriors':'Golden State Warriors', 'Rockets':'Houston Rockets', 'Pacers':'Indiana Pacers',
    'Clippers':'Los Angeles Clippers', 'Lakers':'Los Angeles Lakers', 'Grizzlies':'Memphis Grizzlies',
    'Heat':'Miami Heat', 'Bucks':'Milwaukee Bucks', 'Timberwolves':'Minnesota Timberwolves',
    'Pelicans':'New Orleans Pelicans', 'Knicks':'New York Knicks', 'Thunder':'Oklahoma City Thunder',
    'Magic':'Orlando Magic', '76ers':'Philadelphia 76ers', 'Suns':'Phoenix Suns',
    'Trail Blazers':'Portland Trail Blazers', 'Kings':'Sacramento Kings', 'Spurs':'San Antonio Spurs',
    'Raptors':'Toronto Raptors', 'Jazz':'Utah Jazz', 'Wizards':'Washington Wizards',
}


def _normal(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def build_index(sport: str, records) -> dict[str, dict | None]:
    index: dict[str, dict | None] = {}
    for item in records:
        if not isinstance(item, dict) or (sport == 'nba' and item.get('league') != 'nba'):
            continue
        name = item.get('name_en')
        if not isinstance(name, str) or not name.strip(): continue
        key = _normal(name)
        # 同名记录不根据模糊线索猜测球队身份。
        if key in index:
            index[key] = None
        else:
            index[key] = item
    return index


@lru_cache(maxsize=1)
def _directories():
    root = Path(__file__).parent / 'team_data'
    football = json.loads((root / 'football.json').read_text(encoding='utf-8'))
    basketball = json.loads((root / 'basketball.json').read_text(encoding='utf-8'))
    nba=build_index('nba', basketball)
    for short, full in NBA_SHORT_NAMES.items():
        key,full_key=_normal(short),_normal(full)
        if key not in nba and nba.get(full_key): nba[key]=nba[full_key]
    return {'football': build_index('football', football.values()), 'nba': nba}


def display_team(team) -> dict:
    match = _directories().get(team.sport, {}).get(_normal(team.source_name))
    name_zh, logo, name_source = team.name_zh, team.logo_url, 'manual' if team.name_zh else None
    if match:
        candidate_name, candidate_logo = match.get('name_zh'), match.get('logo')
        if not name_zh and isinstance(candidate_name, str) and candidate_name.strip():
            name_zh, name_source = candidate_name.strip(), 'sportsmatch'
        placeholder = logo == 'https://polymarket-upload.s3.us-east-2.amazonaws.com/soccer-ball.png'
        if (not logo or placeholder) and isinstance(candidate_logo, str) and candidate_logo.startswith('https://'):
            logo = candidate_logo
    return {'id': str(team.id), 'sport': team.sport, 'source_name': team.source_name,
            'name_zh': name_zh, 'name_zh_source': name_source,
            'translation_status': team.translation_status, 'logo_url': logo}
