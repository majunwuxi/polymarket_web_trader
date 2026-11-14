"""只读核验订单所选选项的市场结算；不计算实际盈亏。"""
import re

import httpx

from .sports_sources import SourceError
from .gamma import normal


def compare_moneyline_score(*,sport,question,outcome_label,home,away,home_score,away_score,provider_status,outcome_won):
    """仅对能够严格识别的胜负选项比较比分；其余市场不推断规则。"""
    if not all(isinstance(x,str) and x.strip() for x in (question,outcome_label,home,away)):
        return 'unsupported'
    if type(home_score) is not int or type(away_score) is not int or type(outcome_won) is not bool:
        return 'unsupported'
    h,a=normal(home),normal(away)
    if not h or not a or h==a: return 'unsupported'
    q=normal(question);label=normal(outcome_label)
    if sport=='nba':
        if q not in (f'{h} vs {a}',f'{a} vs {h}') or label not in (h,a) or home_score==away_score:
            return 'unsupported'
        expected=(home_score>away_score) if label==h else (away_score>home_score)
    elif sport=='football' and provider_status=='FT':
        # 仅接受明确的球队获胜命题；加时/点球及其他问题保持待核实。
        matched=re.fullmatch(r'will (.+) win on (\d{4}) (\d{2}) (\d{2})',q)
        if not matched or matched.group(1) not in (h,a) or label not in ('yes','no'):
            return 'unsupported'
        team_won=home_score>away_score if matched.group(1)==h else away_score>home_score
        expected=team_won if label=='yes' else not team_won
    else:
        return 'unsupported'
    return 'consistent' if expected==outcome_won else 'conflict'


def read_resolution(client, condition_id, outcome_index):
    if not isinstance(condition_id,str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}',condition_id):
        raise SourceError('invalid_condition_id')
    if type(outcome_index) is not int or outcome_index not in (0,1): raise SourceError('invalid_outcome_index')
    try:
        response=client.get('https://data-api.polymarket.com/v2/resolutions',params={'condition':condition_id},timeout=15,follow_redirects=False)
        if response.status_code!=200: raise SourceError('http_'+str(response.status_code))
        body=response.json()
    except httpx.TimeoutException as exc: raise SourceError('timeout') from exc
    except (httpx.RequestError,ValueError) as exc: raise SourceError('network_or_invalid_json') from exc
    if not isinstance(body,dict) or not isinstance(body.get('data'),list): raise SourceError('invalid_response')
    rows=body['data']
    if not rows: return {'state':'pending'}
    if len(rows)!=1 or not isinstance(rows[0],dict) or str(rows[0].get('condition_id','')).lower()!=condition_id.lower():
        raise SourceError('resolution_identity_mismatch')
    row=rows[0];payouts=row.get('payouts')
    # 非终态、分数赔付或特殊结算不得猜测结果。
    if str(row.get('status','')).lower() not in ('resolved','finalized','settled') or not row.get('resolved_at') or not isinstance(payouts,list) or len(payouts)!=2 or any(type(x) is not int for x in payouts) or payouts not in ([0,1],[1,0]):
        return {'state':'pending'}
    return {'state':'resolved','outcome_won':payouts[outcome_index]==1,'resolved_at':row['resolved_at']}
