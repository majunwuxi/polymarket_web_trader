"""均为模拟结算数据，不表示真实市场或真实交易。"""
from decimal import Decimal
import httpx
import pytest

from backend.order_resolution import (compare_moneyline_score, compare_score, judge_moneyline_score,
                                      judge_score, judge_score_detail, read_resolution, settlement_amounts)
from backend.sports_sources import SourceError

CONDITION='0x'+'a'*64


@pytest.mark.parametrize(('scores', 'label', 'status', 'expected'), [
    ((0, 0), 'Yes', 'FT', True),
    ((2, 2), 'No', 'FT', False),
    ((2, 1), 'Yes', 'FT', False),
    ((0, 1), 'No', 'FT', True),
    ((1, 1), 'Yes', 'PEN', None),
])
def test_football_full_time_draw_market(scores, label, status, expected):
    result, _ = judge_score_detail(
        sport='football', question='Will Stade Brestois 29 vs. Paris FC end in a draw?',
        outcome_label=label, home='Stade Brestois 29', away='Paris FC',
        home_score=scores[0], away_score=scores[1], provider_status=status,
    )
    assert result is expected


def read(body,index=0):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body))) as client:
        return read_resolution(client,CONDITION,index)


def test_resolution_needs_terminal_binary_payout_and_identity():
    row={'condition_id':CONDITION,'status':'resolved','resolved_at':'2026-01-01T00:00:00Z','payouts':[1,0]}
    assert read({'data':[row]})['outcome_won'] is True
    assert read({'data':[row]},1)['outcome_won'] is False
    assert read({'data':[]})['state']=='pending'
    assert '结算数据暂未收录' in read({'data':[]})['market_status_text']
    # resolved_at 仅用于展示；结算判据是终态 status + 可用的二元赔付。
    assert read({'data':[{**row,'resolved_at':None}]})['state']=='resolved'
    assert read({'data':[{**row,'status':'proposed'}]})['state']=='pending'
    assert read({'data':[{**row,'payouts':[1,1]}]})['state']=='pending'
    with pytest.raises(SourceError,match='resolution_identity_mismatch'):
        read({'data':[{**row,'condition_id':'0x'+'b'*64}]})


def test_official_micro_usdc_payouts_are_accepted():
    """官方 /v2/resolutions 的 payouts 为 micro-USDC/份（1 pUSD = 1_000_000），不是 0/1。"""
    row={'condition_id':CONDITION,'status':'resolved','resolved_at':'2026-11-15T02:00:00Z',
         'payouts':[1000000,0],'reporter':'UMA_OO'}
    first=read({'data':[row]})
    assert first['state']=='resolved' and first['outcome_won'] is True
    assert first['market_status']=='resolved' and first['resolved_at']=='2026-11-15T02:00:00Z'
    assert first['payouts']==[1000000,0] and first['reporter']=='UMA_OO'
    assert read({'data':[row]},1)['outcome_won'] is False
    assert read({'data':[{**row,'payouts':[0,1000000]}]},1)['outcome_won'] is True
    assert read({'data':[{**row,'payouts':['0','1000000']}]},0)['outcome_won'] is False
    assert read({'data':[{**row,'status':'settled'}]})['state']=='resolved'


def test_resolution_rows_report_stage_and_refuse_unusable_payouts():
    resolved={'condition_id':CONDITION,'status':'resolved','resolved_at':'2026-11-15T02:00:00Z','payouts':[1000000,0]}
    proposed={'condition_id':CONDITION,'status':'proposed','resolved_at':None,'payouts':None}
    # condition 选择器可能返回多行（V2 生命周期 → UMA 生命周期 → CTF 终态）。
    assert read({'data':[proposed,resolved]})['state']=='resolved'
    stage=read({'data':[proposed]})
    assert stage['state']=='pending' and stage['market_status']=='proposed'
    assert '已提议结果' in stage['market_status_text']
    no_payout=read({'data':[{**resolved,'payouts':None}]})
    assert no_payout['state']=='pending' and '赔付明细' in no_payout['market_status_text']
    for payouts in ([1,1],[500000,500000],[0,0],[2,0],[1.5,0],[None,1]):
        assert read({'data':[{**resolved,'payouts':payouts}]})['state']=='pending'


def test_conflicting_terminal_rows_are_not_silently_picked():
    a={'condition_id':CONDITION,'status':'resolved','payouts':[1000000,0]}
    b={'condition_id':CONDITION,'status':'resolved','payouts':[0,1000000]}
    with pytest.raises(SourceError,match='resolution_conflict'):
        read({'data':[a,b]})


def test_score_comparison_refuses_ambiguous_market_and_flags_conflict():
    common={'sport':'nba','question':'Nuggets vs. Pelicans','home':'Pelicans','away':'Nuggets',
            'outcome_label':'Nuggets','home_score':90,'away_score':100,'provider_status':'final'}
    assert compare_moneyline_score(**common,outcome_won=True)=='consistent'
    assert compare_moneyline_score(**common,outcome_won=False)=='conflict'
    assert compare_moneyline_score(**{**common,'question':'Nuggets total points over 99'},outcome_won=True)=='unsupported'
    football={'sport':'football','question':'Will Norway win on 2026-11-14?','home':'Norway','away':'Wales',
              'outcome_label':'Yes','home_score':1,'away_score':0,'provider_status':'FT'}
    assert compare_moneyline_score(**football,outcome_won=True)=='consistent'
    assert compare_moneyline_score(**{**football,'provider_status':'AET'},outcome_won=True)=='unsupported'


def test_score_judgement_is_independent_of_market_result():
    nba={'sport':'nba','question':'Nuggets vs. Pelicans','home':'Pelicans','away':'Nuggets',
         'outcome_label':'Nuggets','home_score':90,'away_score':100,'provider_status':'final'}
    assert judge_moneyline_score(**nba) is True
    assert judge_moneyline_score(**{**nba,'outcome_label':'Pelicans'}) is False
    assert judge_moneyline_score(**{**nba,'question':'Nuggets total points over 99'}) is None
    assert judge_moneyline_score(**{**nba,'home_score':None}) is None
    football={'sport':'football','question':'Will Norway win on 2026-11-14?','home':'Norway','away':'Wales',
              'outcome_label':'Yes','home_score':1,'away_score':0,'provider_status':'FT'}
    assert judge_moneyline_score(**football) is True
    assert judge_moneyline_score(**{**football,'outcome_label':'No'}) is False
    assert judge_moneyline_score(**{**football,'provider_status':'AET'}) is None


def settle(**kwargs):
    values=dict(side='BUY',matched_size=Decimal('3'),fills=[],market_outcome_won=True,result_outcome_won=True)
    return settlement_amounts(**(values|kwargs))


def test_two_results_agree_and_confirm_settlement_amount():
    win=settle()
    assert win['agreement']=='consistent' and win['settlement_amount']=='3' and win['currency']=='pUSD'
    assert win['market_settlement_amount']=='3' and win['result_settlement_amount']=='3'
    assert '一致' in win['notice']
    assert win['cost_basis'] is None and win['realized_pnl'] is None and win['cost_verified'] is False
    assert '成交明细' in win['reason']
    assert settle(market_outcome_won=False,result_outcome_won=False)['settlement_amount']=='0'


def test_conflicting_results_keep_both_amounts_without_confirming():
    clash=settle(result_outcome_won=False)
    assert clash['agreement']=='conflict' and clash['settlement_amount'] is None
    assert clash['market_settlement_amount']=='3' and clash['result_settlement_amount']=='0'
    assert clash['cost_basis'] is None and '人工核实' in clash['reason']


def test_single_result_is_labelled_and_never_assumed_to_agree():
    market_only=settle(result_outcome_won=None)
    assert market_only['agreement']=='market_only' and market_only['settlement_amount']=='3'
    assert '人工核实' in market_only['notice']
    result_only=settle(market_outcome_won=None)
    assert result_only['agreement']=='result_only' and result_only['result_settlement_amount']=='3'
    assert result_only['settlement_amount'] is None and '预计' in result_only['reason']
    assert settle(market_outcome_won=None,result_outcome_won=None)['agreement']=='unavailable'


def test_settlement_cost_and_pnl_require_complete_confirmed_fills():
    fills=[{'size':Decimal('2'),'price':Decimal('0.4'),'status':'CONFIRMED'},
           {'size':Decimal('1'),'price':Decimal('0.5'),'status':'CONFIRMED'}]
    result=settle(fills=fills)
    assert result['settlement_amount']=='3' and result['cost_basis']=='1.3' and result['realized_pnl']=='1.7'
    assert result['covered_shares']=='3' and result['confirmed_fill_count']==2 and result['cost_verified'] is True
    for partial in (fills[:1],[{**fills[0],'status':'PENDING'},fills[1]]):
        assert settle(fills=partial)['cost_basis'] is None and settle(fills=partial)['cost_verified'] is False
    assert settle(fills=fills,market_outcome_won=False,result_outcome_won=False)['realized_pnl']=='-1.3'


def test_settlement_refuses_sell_and_missing_shares():
    assert settle(side='SELL')['settlement_amount'] is None and '卖出' in settle(side='SELL')['reason']
    assert settle(matched_size=Decimal('0'))['settlement_amount'] is None
    assert settle(matched_size=None)['settlement_amount'] is None


# 截图实例：NBA 全场大小分 227.5，比分 127:117，总分 244。
OU=dict(sport='nba',question='Mavericks vs. Cavaliers: O/U 227.5',
        home='Cleveland Cavaliers',away='Dallas Mavericks',
        home_score=127,away_score=117,provider_status='final')


def test_totals_market_is_judged_from_final_score():
    won,basis=judge_score_detail(**OU,outcome_label='Under')
    assert won is False and '244' in basis and '227.5' in basis and 'Under' in basis
    assert judge_score(**OU,outcome_label='Over') is True
    assert compare_score(**OU,outcome_label='Under',outcome_won=False)=='consistent'
    assert compare_score(**OU,outcome_label='Under',outcome_won=True)=='conflict'


def test_totals_accepts_explicit_variants_and_refuses_push():
    variants=[dict(question='Mavericks vs. Cavaliers: Over/Under 227.5',outcome_label='Over'),
              dict(question='Mavericks vs. Cavaliers: O/U 227.5',outcome_label='Under 227.5'),
              dict(question='Will there be over 227.5 points in Mavericks vs Cavaliers?',outcome_label='Yes'),
              dict(question='Will there be over 227.5 points in Mavericks vs Cavaliers?',outcome_label='No')]
    assert [judge_score(**(OU|variant)) for variant in variants]==[True,False,True,False]
    push,basis=judge_score_detail(**(OU|dict(question='Mavericks vs. Cavaliers: O/U 244',outcome_label='Under')))
    assert push is None and '走盘' in basis
    assert judge_score_detail(**(OU|dict(outcome_label='Under 230.5')))[0] is None


def test_partial_and_unclear_markets_are_not_guessed():
    partial,basis=judge_score_detail(**(OU|dict(question='Mavericks vs. Cavaliers: First Half O/U 110.5',outcome_label='Under')))
    assert partial is None and '半场' in basis
    assert judge_score(**(OU|dict(question='Mavericks vs. Cavaliers: Spread',outcome_label='Yes'))) is None
    assert judge_score(**(OU|dict(question='LeBron James: O/U 27.5 points',outcome_label='Under'))) is None
    assert judge_score(**(OU|dict(home_score=None,outcome_label='Under'))) is None


def test_spread_only_when_outcome_states_the_handicap():
    spread=OU|dict(question='Mavericks vs. Cavaliers: Spread')
    won,basis=judge_score_detail(**spread,outcome_label='Cavaliers -4.5')
    assert won is True and '覆盖' in basis
    assert judge_score(**spread,outcome_label='Mavericks +4.5') is False
    push,basis=judge_score_detail(**(spread|dict(outcome_label='Cavaliers -10')))
    assert push is None and '走盘' in basis


def test_football_totals_require_full_time():
    football=dict(sport='football',question='Will there be over 2.5 goals in Norway vs Wales?',
                  outcome_label='Yes',home='Norway',away='Wales',home_score=2,away_score=1,provider_status='FT')
    assert judge_score(**football) is True
    assert judge_score(**(football|dict(outcome_label='No'))) is False
    assert judge_score(**(football|dict(provider_status='AET'))) is None
