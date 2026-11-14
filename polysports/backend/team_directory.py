"""仅用于显示的用户维护球队资料；不参与赛事或交易身份验证。"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import unicodedata


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
    return {'football': build_index('football', football.values()), 'nba': build_index('nba', basketball)}


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
