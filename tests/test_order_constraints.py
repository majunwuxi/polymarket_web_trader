import unittest
from decimal import Decimal
from unittest.mock import Mock, patch
from types import SimpleNamespace
from polymarket_autotrader.execution import CLOBOrderExecutor
import dashboard_api

class OrderConstraintTests(unittest.TestCase):
    def test_price_and_size_boundaries(self):
        validate = CLOBOrderExecutor._validate_order_constraints
        tick, minimum = Decimal('0.0025'), Decimal('5')
        for price, size in [('0.0025','5'), ('0.9975','5.01'), ('0.52','10')]:
            validate(price, size, tick, minimum)
        for price, size in [('0.003','5'), ('1','5'), ('0','5'), ('0.52','4.99'), ('0.52','5.001'), ('NaN','5'), ('0.52','Infinity')]:
            with self.subTest(price=price, size=size), self.assertRaises(ValueError):
                validate(price, size, tick, minimum)

    def test_invalid_orders_and_simulation_require_rules(self):
        executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        executor.dry_run = True
        executor._get_book_context = Mock(return_value=('v2',Decimal('0.01'),Decimal('5')))
        executor._get_client = Mock()
        self.assertEqual(executor.place_order('BUY',0.515,10,'m','123')['status'], 'failed')
        self.assertEqual(executor.place_order('BUY',0.51,4.99,'m','123')['status'], 'failed')
        self.assertEqual(executor.place_order('BUY',0.51,5,'m','123')['status'], 'simulated')
        executor._get_book_context.side_effect = ValueError('missing rules')
        self.assertEqual(executor.place_order('BUY',0.51,5,'m','123')['status'], 'failed')
        executor._get_client.assert_not_called()

    def test_sell_percentage_rounds_down_and_propagates_error(self):
        data = Mock()
        data.fetch_positions_for_user.return_value = [{'asset':'123','size':10.019}]
        data.get_order_book.return_value = SimpleNamespace(bids=[{'price':'0.5'}])
        executor = Mock()
        executor.place_order.return_value = {'orderId':'abc','status':'pending'}
        wallet = SimpleNamespace(name='test', address='0x123')
        with patch.object(dashboard_api,'data_client',data), patch.object(dashboard_api,'clob_executor',executor), patch.object(dashboard_api,'wallet_executors',{}), patch.object(dashboard_api,'get_current_wallet',return_value=wallet), patch.object(dashboard_api,'get_dashboard_wallet_address',return_value='0x123'):
            with dashboard_api.app.test_request_context('/api/trade/sell', method='POST', json={'token_id':'123','percentage':0.99}):
                response = dashboard_api.api_trade_sell()
            self.assertEqual(response.status_code,200)
            self.assertEqual(executor.place_order.call_args.kwargs['size'], 9.91)
            executor.place_order.return_value = {'orderId':'','status':'failed','error':'minimum 5'}
            with dashboard_api.app.test_request_context('/api/trade/sell', method='POST', json={'token_id':'123','percentage':0.1}):
                response, status = dashboard_api.api_trade_sell()
            self.assertEqual(status,400)
            self.assertEqual(response.json['error'],'minimum 5')

if __name__ == '__main__': unittest.main()
