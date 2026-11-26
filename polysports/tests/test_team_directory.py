"""球队目录显示测试；模拟输入不代表实时赛事。"""
from types import SimpleNamespace
from uuid import uuid4

from backend.team_directory import NBA_SHORT_NAMES, build_index, display_team, football_name_variants


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


def test_football_directory_exact_aliases_are_bidirectional():
    assert football_name_variants('North Macedonia') >= {'north macedonia','fyr macedonia','macedonia'}
    assert 'north macedonia' in football_name_variants('FYR Macedonia')
    assert football_name_variants('Unknown National Team') == set()


def test_football_directory_aliases_display_source_full_names():
    # 巴甲 Gamma 使用球队全名；别名键同样命中显示资料。
    flamengo=display_team(team('CR Flamengo'))
    assert flamengo['name_zh']=='弗拉门戈' and flamengo['name_zh_source']=='sportsmatch'
    assert flamengo['logo_url']=='https://media.api-sports.io/football/teams/127.png'
    assert display_team(team('Clube do Remo'))['name_zh']=='雷莫'
    assert display_team(team('São Paulo FC'))['name_zh']=='圣保罗'


def test_football_directory_serie_a_polymarket_names_show_chinese():
    expected={
        'AC Milan':'AC米兰', 'AC Monza':'蒙扎', 'ACF Fiorentina':'佛罗伦萨',
        'AS Roma':'罗马', 'Atalanta BC':'亚特兰大', 'Bologna FC 1909':'博洛尼亚',
        'Cagliari Calcio':'卡利亚里', 'Como 1907':'科莫', 'FC Internazionale Milano':'国际米兰',
        'Frosinone Calcio':'弗洛西诺', 'Genoa CFC':'热那亚', 'Juventus FC':'尤文图斯',
        'Parma Calcio 1913':'帕尔马', 'SS Lazio':'拉齐奥', 'SSC Napoli':'那不勒斯',
        'Torino FC':'都灵', 'US Lecce':'莱切', 'US Sassuolo Calcio':'萨索洛',
        'Udinese Calcio':'乌迪内斯', 'Venezia FC':'威尼斯',
    }
    for source,name_zh in expected.items():
        result=display_team(team(source))
        assert result['name_zh']==name_zh and result['name_zh_source']=='sportsmatch', source


def test_football_directory_acle_polymarket_names_show_chinese():
    # 亚冠精英：Polymarket Gamma `league=acle` 的 35 支现役球队必须都能显示中文名。
    expected={
        'Al Ahli Saudi FC':'吉达国民', 'Al Ain FC':'艾因', 'Al Gharafa SC':'加拉法',
        'Al Hilal SFC':'利雅得新月', 'Al Hussein Irbid':'伊尔比德侯赛因',
        'Al Jazira (UAE)':'阿布扎比半岛', 'Al Nassr Club':'利雅得胜利',
        'Al Qadsiah':'胡拜尔库迪西亚', 'Al Quwa Al Jawiya':'巴格达空军', 'Al Sadd SC':'萨德',
        'Al Wasl FC':'迪拜瓦斯尔', 'Al-Ittihad Club':'吉达联合', 'Al-Shamal':'沙马尔',
        'Beijing Guoan':'北京国安', 'Buriram United':'武里南联', 'Cong An Ha Noi FC':'河内公安',
        'Daejeon Citizen FC':'大田市民', 'Esteghlal FC Tehran':'德黑兰独立',
        'FC Pohang Steelers':'浦项制铁', 'FK Neftchi Fargona':'费尔干纳纳夫奇',
        'Gamba Osaka':'大阪钢巴', 'Gangwon FC':'江原FC', 'Jeonbuk FC':'全北现代',
        "Johor Darul Ta'zim":'柔佛新山', 'Kashima Antlers':'鹿岛鹿角', 'Kashiwa Reysol':'柏太阳神',
        'Kyoto Sanga FC':'京都不死鸟', 'Newcastle United Jets':'纽卡斯尔喷气机',
        'Pakhtakor':'塔什干棉农', 'Port FC':'泰港', 'Ratchaburi FC':'叻丕府',
        'Shabab Al Ahli Dubai':'迪拜青年国民', 'Shanghai Port FC':'上海海港',
        'Tractor Tabriz':'大不里士拖拉机', 'Vissel Kobe':'神户胜利船',
    }
    assert len(expected)==35
    for source,name_zh in expected.items():
        result=display_team(team(source))
        assert result['name_zh']==name_zh and result['name_zh_source']=='sportsmatch', source
        assert result['logo_url'] and result['logo_url'].startswith('https://'), source


def test_football_directory_acle_api_names_share_polymarket_aliases():
    # 赛果绑定用 API-Football 名，显示用 Polymarket 名；两者必须是同一支球队的精确别名。
    pairs={
        'Kashima':'Kashima Antlers', 'Kyoto Sanga':'Kyoto Sanga FC',
        'Daejeon Citizen':'Daejeon Citizen FC', 'Jeonbuk Motors':'Jeonbuk FC',
        'Pohang Steelers':'FC Pohang Steelers', 'SHANGHAI SIPG':'Shanghai Port FC',
        'Al-Hilal Saudi FC':'Al Hilal SFC', 'Al-Ahli Jeddah':'Al Ahli Saudi FC',
        'Al-Nassr':'Al Nassr Club',
    }
    for api_name,polymarket_name in pairs.items():
        # API-Football 名与 Polymarket 名必须解析到同一组归一化别名。
        assert football_name_variants(api_name)==football_name_variants(polymarket_name), api_name
        assert football_name_variants(api_name), api_name


def test_football_directory_backfilled_teams_share_polymarket_aliases():
    # API-Football 名与 Polymarket 队名必须解析到同一组归一化别名。
    pairs={
        'Aalesund':'Aalesunds FK', 'Malaga':'Málaga CF',
        'Deportivo La Coruna':'RC Deportivo A Coruña', 'Maritimo':'CS Marítimo',
        'Academico Viseu':'Académico de Viseu FC', 'Degerfors IF':'Degerfors IF',
        'Orgryte IS':'Örgryte IS', 'PK-35 Vantaa':'PK-35',
        'Turku PS':'TPS Turku', 'Aldosivi':'CA Aldosivi',
    }
    for api_name,polymarket_name in pairs.items():
        assert football_name_variants(api_name)==football_name_variants(polymarket_name), api_name
        assert football_name_variants(api_name), api_name


def test_football_directory_uecl_polymarket_names_show_chinese():
    # 欧协联：Polymarket 队名必须都能显示中文名与 API-Football 队徽。
    expected={
        'Aarhus GF':'奥胡斯', 'FC København':'哥本哈根', 'FC Nordsjælland':'北西兰',
        'FC Thun':'图恩', 'FK Jablonec':'亚布洛内茨', 'FK Kauno Žalgiris':'考纳斯扎尔吉里斯',
        'HNK Hajduk Split':'哈伊杜克斯普利特', 'Heart of Midlothian FC':'哈茨',
        "Inter Club d'Escaldes":'伊斯卡尔德斯国际', 'K. Sint-Truidense VV':'圣图尔登',
        'KAA Gent':'根特', 'PFK CSKA Sofia':'索菲亚中央陆军', 'Panathinaikós AO':'帕纳辛奈科斯',
        'Páfos FC':'帕福斯', 'Qairat FK':'阿拉木图凯拉特', 'Rīga FC':'里加FC',
        'SK Iberia 1999':'第比利斯伊比利亚1999', 'Universitatea Craiova CS':'克拉约瓦大学',
    }
    assert len(expected)==18
    for source,name_zh in expected.items():
        result=display_team(team(source))
        assert result['name_zh']==name_zh and result['name_zh_source']=='sportsmatch', source
        assert result['logo_url'] and result['logo_url'].startswith('https://media.api-sports.io/'), source


def test_football_directory_uel_polymarket_names_show_chinese():
    # 欧罗巴：Polymarket 队名必须都能显示中文名与 API-Football 队徽。
    expected={
        'AS Omónoia Leukosías':'奥莫尼亚', 'Ararat-Armenia FA':'阿拉拉特亚美尼亚',
        'FC Viktoria Plzeň':'比尔森胜利', 'KKS Lech Poznań':'波兹南莱赫',
        "MH Hapoel Be'er Sheva":'贝尔谢巴工人', 'OFI':'OFI克里特',
        'Olympiakós SFP':'奥林匹亚科斯', 'PFK Levski Sofia':'索菲亚列夫斯基',
        'RSC Anderlecht':'安德莱赫特', 'SC União Torreense':'托伦斯',
    }
    assert len(expected)==10
    for source,name_zh in expected.items():
        result=display_team(team(source))
        assert result['name_zh']==name_zh and result['name_zh_source']=='sportsmatch', source
        assert result['logo_url'] and result['logo_url'].startswith('https://media.api-sports.io/'), source


def test_football_directory_uecl_uel_api_names_share_polymarket_aliases():
    # 赛果绑定用 API-Football 名，赛事中心显示用 Polymarket 名；两者必须解析到同一组别名。
    pairs={
        'Aarhus':'Aarhus GF', 'FC Copenhagen':'FC København',
        'FC Nordsjaelland':'FC Nordsjælland', 'FC Thun':'FC Thun', 'FK Jablonec':'FK Jablonec',
        'Kauno Žalgiris':'FK Kauno Žalgiris', 'HNK Hajduk Split':'HNK Hajduk Split',
        'Heart Of Midlothian':'Heart of Midlothian FC',
        "Inter Club d'Escaldes":"Inter Club d'Escaldes", 'St. Truiden':'K. Sint-Truidense VV',
        'Gent':'KAA Gent', 'CSKA Sofia':'PFK CSKA Sofia', 'Panathinaikos':'Panathinaikós AO',
        'Pafos':'Páfos FC', 'Kairat Almaty':'Qairat FK', 'Riga':'Rīga FC',
        'FC Iberia 1999':'SK Iberia 1999', 'Universitatea Craiova':'Universitatea Craiova CS',
        'Omonia Nicosia':'AS Omónoia Leukosías', 'Ararat-Armenia':'Ararat-Armenia FA',
        'Plzen':'FC Viktoria Plzeň', 'Lech Poznan':'KKS Lech Poznań',
        'Hapoel Beer Sheva':"MH Hapoel Be'er Sheva", 'OFI':'OFI',
        'Olympiakos Piraeus':'Olympiakós SFP', 'Levski Sofia':'PFK Levski Sofia',
        'Anderlecht':'RSC Anderlecht', 'Torreense':'SC União Torreense',
    }
    assert len(pairs)==28
    for api_name,polymarket_name in pairs.items():
        assert football_name_variants(api_name)==football_name_variants(polymarket_name), api_name
        assert football_name_variants(api_name), api_name


def test_football_alias_collisions_stay_conservative():
    # 别名与另一支球队的键冲突时，冲突键回退为不匹配，不猜测归属。
    alpha={'name_en':'Alpha FC','name_zh':'甲队','aliases':['Shared Name']}
    beta={'name_en':'Shared Name','name_zh':'乙队'}
    index=build_index('football',[alpha,beta],include_aliases=True)
    assert index['shared name'] is None
    assert index['alpha fc']['name_zh']=='甲队'
    # 同一记录的名称与别名重复不视为冲突。
    gamma={'name_en':'Gamma FC','name_zh':'丙队','aliases':['Gamma FC']}
    assert build_index('football',[gamma],include_aliases=True)['gamma fc']['name_zh']=='丙队'
    # 未开启别名索引时别名键不进入索引；NBA 目录不受影响。
    assert 'shared name' not in build_index('football',[alpha])
    assert 'shared name' not in build_index('nba',[{**alpha,'league':'nba'}])


def test_polymarket_nba_short_names_have_chinese_and_keep_source_icon():
    assert len(NBA_SHORT_NAMES)==30
    for short,full in NBA_SHORT_NAMES.items():
        record=team(short,sport='nba',logo_url='https://example.test/source-icon.png')
        result=display_team(record)
        assert result['name_zh']==display_team(team(full,sport='nba'))['name_zh']
        assert result['name_zh']
        assert result['source_name']==short
        assert result['logo_url']=='https://example.test/source-icon.png'
    assert display_team(team('Pelicans',sport='football'))['name_zh'] is None
    assert display_team(team('Pelican',sport='nba'))['name_zh'] is None
