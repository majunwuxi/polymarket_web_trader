import unittest
from types import SimpleNamespace
from polymarket_autotrader.execution import CLOBOrderExecutor

class OrderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.executor = CLOBOrderExecutor.__new__(CLOBOrderExecutor)
        self.executor.dry_run = False

    def test_submission_requires_order_id_and_never_invents_fill(self):
        check = self.executor._validate_order_response
        self.assertEqual(check({'success': True, 'orderID': 'abc', 'status': 'live'}, 10), (True, None, None))
        self.assertEqual(check({'orderID': 'abc', 'size_matched': '0'}, 10), (True, 0.0, None))
        self.assertEqual(check({'orderID': 'abc', 'size_matched': '3'}, 10), (True, 3.0, None))
        self.assertFalse(check({'success': False, 'orderID': 'abc'}, 10)[0])
        self.assertFalse(check({'success': True}, 10)[0])
        self.assertFalse(check(None, 10)[0])

    def test_cancel_requires_confirmation(self):
        self.executor.client = SimpleNamespace(cancel_orders=lambda ids: {'canceled': ['abc'], 'not_canceled': {}})
        self.assertTrue(self.executor.cancel_order('abc')['success'])
        self.executor.client = SimpleNamespace(cancel_orders=lambda ids: {'canceled': [], 'not_canceled': {'abc': 'already matched'}})
        result = self.executor.cancel_order('abc')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'already matched')

if __name__ == '__main__':
    unittest.main()
