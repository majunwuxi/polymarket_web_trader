import unittest
from types import SimpleNamespace
from decimal import Decimal
from unittest.mock import Mock
from polymarket_autotrader.execution import CLOBOrderExecutor

class OrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        self.executor.dry_run = False
        self.executor._get_book_context = Mock(return_value=('v1', Decimal('0.01'), Decimal('5')))
        self.client = Mock()
        self.executor._get_client = Mock(return_value=self.client)

    def test_submission_requires_order_id_and_never_invents_fill(self):
        self.client.post_order.return_value = SimpleNamespace(ok=True, order_id='abc')
        result = self.executor.place_order('BUY', 0.5, 10, 'm', '123')
        self.assertEqual(result, {'orderId':'abc','status':'pending','filled_size':None})
        self.client.post_order.return_value = SimpleNamespace(ok=False, message='rejected')
        self.assertEqual(self.executor.place_order('BUY',0.5,10,'m','123')['status'],'failed')
        self.client.post_order.return_value = SimpleNamespace(ok=True, order_id='')
        self.assertEqual(self.executor.place_order('BUY',0.5,10,'m','123')['status'],'failed')

    def test_cancel_requires_confirmation(self):
        self.client.cancel_order.return_value = SimpleNamespace(canceled=('abc',), not_canceled={})
        self.assertTrue(self.executor.cancel_order('abc')['success'])
        self.client.cancel_order.return_value = SimpleNamespace(canceled=(), not_canceled={'abc':'already matched'})
        result = self.executor.cancel_order('abc')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'already matched')

    def test_get_order_preserves_matched_quantity(self):
        self.client.get_order.return_value = SimpleNamespace(id='abc', status='LIVE', size_matched=Decimal('2.5'))
        self.assertEqual(self.executor.get_order('abc')['size_matched'], '2.5')

if __name__ == '__main__': unittest.main()
