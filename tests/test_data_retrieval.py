import unittest
from unittest.mock import Mock, patch
import requests
import dashboard_api
from polymarket_autotrader.clients import PolymarketDataClient


def response(payload):
    r=Mock(status_code=200)
    r.json.return_value=payload
    return r

class DataRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.client=PolymarketDataClient.__new__(PolymarketDataClient)
        self.client.data_api_base_url='https://data.example'
        self.client.gamma_base_url='https://gamma.example'
        self.client.session=Mock()
        self.client.retry_config=Mock()

    @patch('polymarket_autotrader.clients.retry_request')
    def test_position_pagination_and_mapping(self, fetch):
        row={'token_id':'123','condition_id':'0xabc','current_size':5.25,'avg_price':0.2,'current_price':0.3,'current_value':1.575,'entry_cost_usdc':1.05,'unrealized_pnl':0.525,'percent_pnl':50}
        fetch.side_effect=[response({'data':[row],'pagination':{'has_more':True,'next_cursor':'next'}}),response({'data':[],'pagination':{'has_more':False,'next_cursor':None}})]
        positions=self.client.fetch_positions_for_user('0xABC')
        self.assertEqual(positions[0]['size'],5.25)
        self.assertEqual(positions[0]['asset'],'123')
        self.assertEqual(positions[0]['cashPnl'],0.525)
        self.assertEqual(fetch.call_args_list[1].kwargs['params']['cursor'],'next')
        self.assertEqual(fetch.call_args_list[1].kwargs['params']['user'],'0xabc')
        self.assertIn('/v2/positions',fetch.call_args_list[0].args[2])

    @patch('polymarket_autotrader.clients.retry_request')
    def test_empty_and_failed_pages(self, fetch):
        fetch.return_value=response({'data':[],'pagination':{'has_more':False,'next_cursor':None}})
        self.assertEqual(self.client.fetch_positions_for_user('0xabc'),[])
        fetch.side_effect=[response({'data':[{'token_id':'1','current_size':2}],'pagination':{'has_more':True,'next_cursor':'x'}}),requests.ConnectionError('down')]
        with self.assertRaises(RuntimeError): self.client.fetch_positions_for_user('0xabc')
        fetch.side_effect=[response({'data':[],'pagination':{'has_more':True,'next_cursor':'x'}}),response({'data':[],'pagination':{'has_more':True,'next_cursor':'x'}})]
        with self.assertRaises(RuntimeError): self.client.fetch_positions_for_user('0xabc')
        fetch.side_effect=None
        fetch.return_value=response({'data':[]})
        with self.assertRaises(RuntimeError): self.client.fetch_positions_for_user('0xabc')

    @patch('polymarket_autotrader.clients.retry_request')
    def test_gamma_identifier_types_are_verified(self, fetch):
        market={'id':'42','conditionId':'0xabc','version':'v2','outcomes':['Yes','No'],'positionIds':['123','456']}
        fetch.return_value=response([market])
        self.assertEqual(self.client._find_market_by_filter('condition_ids','0xabc','123')['id'],'42')
        self.assertEqual(self.client._find_market_by_filter('condition_ids','0xwrong','123'),{})
        self.assertEqual(self.client._find_market_by_filter('position_ids','456')['id'],'42')
        self.assertEqual(self.client._find_market_by_filter('position_ids','999'),{})
        self.assertEqual(fetch.call_args.kwargs['params']['version'],'v2')
        fetch.return_value=response([market,market])
        self.assertEqual(self.client._find_market_by_filter('position_ids','123'),{})

    def test_ambiguous_event_never_selects_first(self):
        self.client.get_event_for_slug=Mock(return_value={'markets':[{'id':'1','outcomes':['Yes'],'clobTokenIds':['10']},{'id':'2','outcomes':['Yes'],'clobTokenIds':['20']}]})
        self.assertIsNone(self.client.get_market_id_for_event_slug('slug'))
        self.assertEqual(self.client.get_market_id_for_event_slug('slug','20'),'2')
        self.assertIsNone(self.client.get_market_id_for_event_slug('slug','99'))

    def test_zero_value_position_is_not_dropped(self):
        client=Mock()
        client.fetch_positions_for_user.return_value=[{'asset':'123','size':5,'avgPrice':0,'current_price':0,'currentValue':0,'title':'resolved','outcome':'No','market_id':'42'}]
        with patch.object(dashboard_api,'data_client',client), patch.object(dashboard_api,'_get_cash_balance_fast',return_value=None), patch.object(dashboard_api,'_prefetch_market_info_async'), patch.object(dashboard_api,'get_current_wallet',return_value=None):
            result=dashboard_api._build_positions_payload('0xabc')
        self.assertEqual(len(result['positions']),1)
        self.assertEqual(result['positions'][0]['current_value'],0)
        self.assertIsNone(result['cash_balance'])

    def test_refresh_failure_is_not_empty_and_stale_cache_remains(self):
        client=Mock()
        client.fetch_positions_for_user.side_effect=RuntimeError('unavailable')
        with patch.object(dashboard_api,'data_client',client):
            with self.assertRaises(RuntimeError): dashboard_api._build_positions_payload('0xabc')
        with patch.object(dashboard_api,'data_client',client), patch.object(dashboard_api,'positions_cache',None), patch.object(dashboard_api,'positions_cache_ts',0), patch.object(dashboard_api,'positions_last_refresh_error','unavailable'), patch.object(dashboard_api,'_trigger_positions_refresh'), patch.object(dashboard_api,'_get_first_response_timeout_seconds',return_value=0), patch.object(dashboard_api,'get_dashboard_wallet_address',return_value='0xabc'):
            with dashboard_api.app.test_request_context('/api/positions'):
                result,status=dashboard_api.api_positions()
            self.assertEqual(status,503)
            self.assertNotIn('positions',result.json)
        with patch.object(dashboard_api,'data_client',client), patch.object(dashboard_api,'positions_cache',{'positions':[{'token_id':'1'}]}), patch.object(dashboard_api,'positions_cache_ts',1), patch.object(dashboard_api,'_trigger_positions_refresh'):
            with dashboard_api.app.test_request_context('/api/positions'):
                result=dashboard_api.api_positions()
            self.assertTrue(result.json['_meta']['stale'])
            self.assertEqual(result.json['positions'][0]['token_id'],'1')

if __name__ == '__main__': unittest.main()
