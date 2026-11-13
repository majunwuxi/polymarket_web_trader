import unittest
from types import SimpleNamespace
from decimal import Decimal
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
        get.return_value.json.return_value = {'asset_id':'101','version':'v2', 'tick_size':'0.01', 'min_order_size':'5'}
        self.assertEqual(executor._get_book_context('101')[0], 'v2')
        get.return_value.json.return_value = {'asset_id':'101', 'tick_size':'0.01', 'min_order_size':'5'}
        self.assertEqual(executor._get_book_context('101')[0], 'v1')
        get.return_value.json.return_value = {'asset_id':'101','version':'future'}
        with self.assertRaises(ValueError): executor._get_book_context('101')
        get.return_value.json.return_value = {'asset_id':'wrong'}
        with self.assertRaises(ValueError): executor._get_book_context('101')

    def test_both_market_versions_use_unified_client(self):
        executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        executor.dry_run = False
        client = Mock()
        client.post_order.return_value = SimpleNamespace(ok=True, order_id='order')
        executor._get_client = Mock(return_value=client)
        for version in ('v1', 'v2'):
            with self.subTest(version=version):
                executor._get_book_context = Mock(return_value=(version, Decimal('0.0025'), Decimal('5')))
                result = executor.place_order('BUY', 0.5, 10, 'market', '101')
                self.assertEqual(result['status'], 'pending')
                self.assertIsNone(result['filled_size'])
        self.assertEqual(client.create_limit_order.call_count, 2)
        client.create_limit_order.assert_called_with(asset_id='101', side='BUY', price='0.5', size='10')
        executor._get_book_context.side_effect = ValueError('unknown version')
        self.assertEqual(executor.place_order('BUY', 0.5, 10, 'market', '101')['status'], 'failed')
        self.assertEqual(client.post_order.call_count, 2)

if __name__ == '__main__': unittest.main()
