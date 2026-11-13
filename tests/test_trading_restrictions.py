import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from eth_account import Account
from polymarket import RequestRejectedError
import dashboard_api
from polymarket_autotrader.execution import CLOBOrderExecutor
from polymarket_autotrader.config import EnvConfig

class TradingRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        self.executor.dry_run = False
        self.executor._get_book_context = Mock(return_value=('v1', Decimal('0.01'), Decimal('5')))
        self.client = Mock()
        self.executor._get_client = Mock(return_value=self.client)

    def place(self):
        return self.executor.place_order('BUY', 0.5, 10, 'market', '123')

    def test_restarting_submission_not_retried_and_delay_kept(self):
        self.client.post_order.side_effect = RequestRejectedError('maintenance', status=425, restriction='restarting', retry_after=2.5)
        result = self.place()
        self.assertEqual(result['code'],'engine_restarting')
        self.assertEqual(result['retry_after'],2.5)
        self.assertIn('撮合引擎',result['error'])
        self.assertNotIn('凭证',result['error'])
        self.client.post_order.assert_called_once()
        self.client.create_limit_order.assert_called_once()

    def test_restart_during_sign_or_initialization(self):
        error = RequestRejectedError('restart', status=425, restriction='restarting')
        self.client.create_limit_order.side_effect = error
        self.assertEqual(self.place()['code'],'engine_restarting')
        self.client.post_order.assert_not_called()
        self.executor._get_client.side_effect = error
        self.assertEqual(self.place()['code'],'engine_restarting')
        self.client.create_limit_order.assert_called_once()

    def test_raw_orderbook_425_and_nonrestart(self):
        http = requests.Response()
        http.status_code=425
        http.headers['Retry-After']='4'
        self.executor._get_book_context.side_effect=requests.HTTPError(response=http)
        result=self.place()
        self.assertEqual(result['code'],'engine_restarting')
        self.assertEqual(result['retry_after'],4)
        self.executor._get_client.assert_not_called()
        self.executor._get_book_context.side_effect=requests.Timeout('uncertain')
        self.assertNotIn('code',self.place())
        self.executor._get_book_context.side_effect=None
        self.client.post_order.side_effect=requests.Timeout('uncertain 425 text')
        self.assertNotIn('code',self.place())
        self.client.post_order.assert_called_once()

    def test_post_only_restriction_no_automatic_conversion(self):
        self.client.post_order.side_effect=RequestRejectedError('restricted', status=503, restriction='post_only', retry_after=79)
        result=self.place()
        self.assertEqual(result['code'],'post_only_mode')
        self.assertEqual(result['retry_after'],79)
        self.client.create_limit_order.assert_called_once_with(asset_id='123',side='BUY',price='0.5',size='10')
        self.client.post_order.assert_called_once()
        self.client.post_order.side_effect=None
        self.client.post_order.return_value=SimpleNamespace(ok=False,code='post_only_mode',message='restricted')
        self.assertEqual(self.place()['code'],'post_only_mode')
        self.assertNotIn('retry_after',self.place())

    @patch('polymarket.SecureClient.create')
    def test_real_initializer_preserves_sdk_restart(self, create):
        key='0x'+'01'*32
        signer=Account.from_key(key).address
        env=EnvConfig(private_key=key,signature_type=0,funder_address=signer,dry_run=False)
        executor=CLOBOrderExecutor('https://clob.polymarket.com',env,4,4)
        executor._get_book_context=Mock(return_value=('v1',Decimal('0.01'),Decimal('5')))
        create.side_effect=RequestRejectedError('restart',status=425,restriction='restarting',retry_after=3)
        result=executor.place_order('BUY',0.5,10,'m','123')
        self.assertEqual(result['code'],'engine_restarting')
        self.assertEqual(result['retry_after'],3)
        self.assertFalse(executor.has_api_creds)
        create.assert_called_once()

    def test_orderbook_425_propagates_through_both_routes(self):
        from polymarket_autotrader.clients import PolymarketDataClient
        data=PolymarketDataClient.__new__(PolymarketDataClient)
        data.clob_host='https://clob.example'
        data.session=Mock()
        data.retry_config=Mock()
        data.fetch_positions_for_user=Mock(return_value=[{'asset':'123','size':10}])
        http=requests.Response()
        http.status_code=425
        http.headers['Retry-After']='6'
        error=requests.HTTPError(response=http)
        wallet=SimpleNamespace(name='test',address='0x123')
        with patch('polymarket_autotrader.clients.retry_request',side_effect=error), patch.object(dashboard_api,'data_client',data), patch.object(dashboard_api,'clob_executor',Mock()), patch.object(dashboard_api,'wallet_executors',{}), patch.object(dashboard_api,'get_current_wallet',return_value=wallet), patch.object(dashboard_api,'get_dashboard_wallet_address',return_value='0x123'):
            with dashboard_api.app.test_request_context('/api/trade/place',method='POST',json={'token_id':'123','side':'BUY','size':10}):
                response,status=dashboard_api.api_trade_place()
            self.assertEqual(status,503)
            self.assertEqual(response.headers['Retry-After'],'6')
            with dashboard_api.app.test_request_context('/api/trade/sell',method='POST',json={'token_id':'123','percentage':0.5}):
                response,status=dashboard_api.api_trade_sell()
            self.assertEqual(status,503)
            self.assertEqual(response.json['code'],'engine_restarting')

    def test_buy_and_sell_http_responses_preserve_delay(self):
        executor=Mock()
        executor.place_order.return_value={'orderId':'','status':'failed','code':'engine_restarting','error':'撮合引擎正在重启','retry_after':2.5}
        wallet=SimpleNamespace(name='test',address='0x123')
        data=Mock()
        data.fetch_positions_for_user.return_value=[{'asset':'123','size':10}]
        data.get_order_book.return_value=SimpleNamespace(bids=[{'price':'0.5'}])
        with patch.object(dashboard_api,'data_client',data), patch.object(dashboard_api,'clob_executor',executor), patch.object(dashboard_api,'wallet_executors',{}), patch.object(dashboard_api,'get_current_wallet',return_value=wallet), patch.object(dashboard_api,'get_dashboard_wallet_address',return_value='0x123'):
            with dashboard_api.app.test_request_context('/api/trade/place',method='POST',json={'token_id':'123','side':'BUY','size':10,'price':0.5}):
                response,status=dashboard_api.api_trade_place()
            self.assertEqual(status,503)
            self.assertEqual(response.json['code'],'engine_restarting')
            self.assertEqual(response.json['retry_after'],2.5)
            self.assertEqual(response.headers['Retry-After'],'3')
            with dashboard_api.app.test_request_context('/api/trade/sell',method='POST',json={'token_id':'123','percentage':0.5}):
                response,status=dashboard_api.api_trade_sell()
            self.assertEqual(status,503)
            self.assertEqual(response.headers['Retry-After'],'3')
            self.assertEqual(response.json['code'],'engine_restarting')
            executor.place_order.return_value={'orderId':'','status':'failed','error':'invalid 425 shares'}
            with dashboard_api.app.test_request_context('/api/trade/place',method='POST',json={'token_id':'123','side':'BUY','size':10,'price':0.5}):
                response,status=dashboard_api.api_trade_place()
            self.assertEqual(status,400)
            self.assertNotIn('code',response.json)
            self.assertEqual(response.json['error'],'invalid 425 shares')

if __name__ == '__main__': unittest.main()
