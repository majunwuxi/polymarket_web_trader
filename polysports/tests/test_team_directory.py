"""球队目录显示测试；模拟输入不代表实时赛事。"""
from types import SimpleNamespace
from uuid import uuid4

from backend.team_directory import build_index, display_team


def team(name, sport='football', name_zh=None, logo_url=None, translation_status='missing'):
    return SimpleNamespace(id=uuid4(), sport=sport, source_name=name, name_zh=name_zh,
                           logo_url=logo_url, translation_status=translation_status)


def test_user_directory_exact_football_name_and_placeholder():
    record=team('Manchester United', logo_url='https://polymarket-upload.s3.us-east-2.amazonaws.com/soccer-ball.png')
    result=display_team(record)
    assert result['name_zh']=='曼联' and result['name_zh_source']=='sportsmatch'
    assert result['logo_url']=='https://media.api-sports.io/football/teams/33.png'
    assert record.name_zh is None and record.translation_status=='missing'


def test_user_directory_nba_and_manual_values_take_priority():
    record=team('Atlanta Hawks',sport='nba',name_zh='管理员已核对',logo_url='https://example.test/real.png',translation_status='verified')
    result=display_team(record)
    assert result['name_zh']=='管理员已核对' and result['name_zh_source']=='manual'
    assert result['logo_url']=='https://example.test/real.png'
    fallback=display_team(team('Atlanta Hawks',sport='nba'))
    assert fallback['name_zh']=='亚特兰大老鹰' and fallback['logo_url'].startswith('https://cdn.nba.com/')


def test_unknown_ambiguous_and_cross_sport_never_guess():
    unknown=display_team(team('Manchester',sport='football'))
    assert unknown['name_zh'] is None
    assert display_team(team('Atlanta Hawks',sport='football'))['name_zh'] is None
    assert display_team(team('Minnesota Lynx',sport='nba'))['name_zh'] is None
    index=build_index('football',[{'name_en':'Mock Club','name_zh':'模拟甲'}, {'name_en':'  MOCK  CLUB ','name_zh':'模拟乙'}])
    assert index['mock club'] is None
