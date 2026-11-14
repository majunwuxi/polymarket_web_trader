"""下单入口使用模拟标识和模拟选项，不调用真实交易接口。"""
import pytest
from pydantic import ValidationError

from backend.market_api import DirectSelection


def test_direct_selection_requires_numeric_identity_and_exact_index():
    valid={'event_id':'123','market_id':'456','outcome_index':0,'outcome_label':'Mock option'}
    assert DirectSelection(**valid).market_id=='456'
    for changed in ({'event_id':'bad'}, {'market_id':'-1'}, {'outcome_index':'0'},
                    {'outcome_index':-1}, {'outcome_label':''}):
        with pytest.raises(ValidationError): DirectSelection(**{**valid,**changed})
