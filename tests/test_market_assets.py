import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from polymarket_autotrader.clients import PolymarketDataClient
from polymarket_autotrader.execution import CLOBOrderExecutor

class MarketAssetTests(unittest.TestCase):
    def test_asset_selection_and_invalid_data(self):
        parse = PolymarketDataClient._market_asset_ids
        self.assertEqual(parse({'version':'v2','outcomes':'["Yes", "No"]','positionIds':['101','102'], 'clobTokenIds':'["1", "2"]'}), {'Yes':'101','No':'102'})
        self.assertEqual(parse({'outcomes':['Yes','No'], 'clobTokenIds':'["1", "2"]'}), {'Yes':'1','No':'2'})
        self.assertEqual(parse({'version':'v2','outcomes':['Yes','No'],'clobTokenIds':['1','2']}), {})
        self.assertEqual(parse({'version':'v2','outcomes':['Yes','No'],'positionIds':['101']}), {})
        self.assertEqual(parse({'version':'v3','outcomes':['Yes'],'positionIds':['101']}), {})

    @patch('polymarket_autotrader.execution.requests.get')
    def test_book_version(self, get):
        executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        executor.clob_host = 'https://clob.polymarket.com'
        get.return_value.json.return_value = {'asset_id':'101','version':'v2'}
        self.assertEqual(executor._get_book_version('101'), 'v2')
        get.return_value.json.return_value = {'asset_id':'101'}
        self.assertEqual(executor._get_book_version('101'), 'v1')
        get.return_value.json.return_value = {'asset_id':'101','version':'future'}
        with self.assertRaises(ValueError): executor._get_book_version('101')
        get.return_value.json.return_value = {'asset_id':'wrong'}
        with self.assertRaises(ValueError): executor._get_book_version('101')

    def test_live_order_routes_by_book_version(self):
        executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        executor.dry_run = False
        executor.price_precision = 3
        executor.size_precision = 2
        executor._get_book_version = Mock(return_value='v2')
        executor._place_protocol_v2_order = Mock(return_value={'orderId':'order', 'status':'pending'})
        result = executor.place_order('BUY', 0.5, 10, 'market', '101')
        executor._place_protocol_v2_order.assert_called_once_with('101', 'BUY', 0.5, 10)
        self.assertEqual(result['orderId'], 'order')
        executor._get_book_version.side_effect = ValueError('unknown version')
        self.assertEqual(executor.place_order('BUY', 0.5, 10, 'market', '101')['status'], 'failed')
        executor._place_protocol_v2_order.assert_called_once()

    def test_v2_signs_and_posts_selected_asset(self):
        executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        executor.clob_host = 'https://clob.polymarket.com'
        executor._env = SimpleNamespace(funder_address='0xwallet')
        client = Mock()
        executor._protocol_v2_client = client
        client.post_order.return_value = SimpleNamespace(ok=True, order_id='order')
        result = executor._place_protocol_v2_order('101', 'BUY', 0.5, 10)
        client.create_limit_order.assert_called_once_with(asset_id='101', side='BUY', price='0.5', size='10')
        client.post_order.assert_called_once_with(client.create_limit_order.return_value)
        self.assertEqual(result, {'orderId':'order','status':'pending','filled_size':None})
        client.post_order.return_value = SimpleNamespace(ok=False, message='rejected')
        self.assertEqual(executor._place_protocol_v2_order('101','BUY',0.5,10)['status'], 'failed')

if __name__ == '__main__': unittest.main()
