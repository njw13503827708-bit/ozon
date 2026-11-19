import tempfile
import unittest
from pathlib import Path

from backend.db.erp import initialize
from backend.repositories.erp import ErpRepository
from backend.repositories.local_operations import LocalOperationsRepository
from backend.integrations.ozon.order_client import OzonOrderClient
from backend.services.order_sync_service import OrderSyncService
from backend.integrations.ozon.base_client import OzonApiError


def posting(number='100-1', sku=123, price='12.34'):
    return {'posting_number': number, 'order_number': '100', 'status': 'delivering',
        'created_at': '2026-10-01T01:00:00Z', 'customer': {'name': 'PRIVATE', 'phone': 'SECRET'},
        'products': [{'sku': sku, 'offer_id': 'offer-1', 'name': 'Органайзер', 'quantity': 2,
                      'price': {'amount': price, 'currency': 'CNY'}}]}


class OrderCenterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize(self.root)
        erp = ErpRepository(self.root)
        erp.upsert_store({'id': 'a', 'platform': 'OZON'})
        erp.upsert_store({'id': 'b', 'platform': 'OZON'})
        self.repo = LocalOperationsRepository(self.root)
        self.repo.initialize()

    def client(self, pages):
        calls = []
        def transport(endpoint, payload):
            calls.append((endpoint, payload))
            response = pages[payload['cursor']]
            if isinstance(response, Exception):
                raise response
            return response
        return OzonOrderClient('offline', 'offline-key', transport=transport, sleep=lambda _: None), calls

    def test_current_cursor_contract_and_money_are_used(self):
        client, calls = self.client({'': {'has_next': True, 'cursor': 'next', 'postings': [posting()]},
                                    'next': {'has_next': False, 'cursor': '', 'postings': [posting('100-2')]}})
        service = OrderSyncService(self.root, client)
        service.run('a', '2026-10-01', '2026-10-02')
        data = service.read('a', '2026-10-01', '2026-10-02')
        self.assertEqual(2, data['total'])
        self.assertEqual('24.68', data['items'][0]['items'][0]['total'])
        self.assertEqual('CNY', data['items'][0]['items'][0]['currency'])
        self.assertEqual(['/v4/posting/fbs/list'] * 2, [call[0] for call in calls])
        self.assertEqual(100, calls[0][1]['limit'])
        self.assertNotIn('offset', calls[0][1])
        self.assertNotIn('PRIVATE', str(data))
        self.assertNotIn('SECRET', str(data))

    def test_repeat_sync_is_idempotent_and_store_scoped(self):
        client, _ = self.client({'': {'has_next': False, 'cursor': '', 'postings': [posting()]}})
        service = OrderSyncService(self.root, client)
        service.run('a', '2026-10-01', '2026-10-02')
        service.run('a', '2026-10-01', '2026-10-02')
        self.assertEqual(1, service.read('a', '2026-10-01', '2026-10-02')['total'])
        self.assertEqual('NOT_SYNCED', service.read('b', '2026-10-01', '2026-10-02')['availability'])

    def test_bad_cursor_never_replaces_previous_success(self):
        client, _ = self.client({'': {'has_next': False, 'cursor': '', 'postings': [posting()]}})
        service = OrderSyncService(self.root, client)
        service.run('a', '2026-10-01', '2026-10-02')
        bad, _ = self.client({'': {'has_next': True, 'cursor': 'x', 'postings': [posting('101-1')]},
                             'x': {'has_next': True, 'cursor': 'x', 'postings': [posting('102-1')]}})
        with self.assertRaises(ValueError):
            OrderSyncService(self.root, bad).run('a', '2026-10-01', '2026-10-02')
        self.assertEqual('100-1', service.read('a', '2026-10-01', '2026-10-02')['items'][0]['posting_number'])

    def test_missing_and_conflicting_records_refused(self):
        for result in ({'cursor': '', 'postings': []},
                       {'has_next': True, 'cursor': '', 'postings': []},
                       {'has_next': False, 'cursor': '', 'postings': [posting(), posting(price='99')]}):
            client, _ = self.client({'': result})
            with self.assertRaises(ValueError):
                OrderSyncService(self.root, client).run('a', '2026-10-01', '2026-10-02')
        self.assertIsNone(self.repo.snapshot('ORDERS', 'a', '2026-10-01', '2026-10-02'))

    def test_pagination_filters_and_empty_success(self):
        client, _ = self.client({'': {'has_next': False, 'cursor': '', 'postings': [posting(str(i)) for i in range(3)]}})
        service = OrderSyncService(self.root, client)
        service.run('a', '2026-10-01', '2026-10-02')
        self.assertEqual(1, len(service.read('a', '2026-10-01', '2026-10-02', page=2, page_size=2)['items']))
        self.assertEqual(0, service.read('a', '2026-10-01', '2026-10-02', status='delivered')['total'])
        self.assertEqual(3, service.read('a', '2026-10-01', '2026-10-02', q='offer-1')['total'])
        empty, _ = self.client({'': {'has_next': False, 'cursor': '', 'postings': []}})
        OrderSyncService(self.root, empty).run('b', '2026-10-01', '2026-10-02')
        self.assertEqual('AVAILABLE', service.read('b', '2026-10-01', '2026-10-02')['availability'])
        self.assertEqual(0, service.read('b', '2026-10-01', '2026-10-02')['total'])

    def test_retry_and_permission_denial_have_bounded_attempts(self):
        counts = []
        def retry(endpoint, payload):
            counts.append(endpoint)
            if len(counts) < 3:
                raise OzonApiError(endpoint, 'offline', 429)
            return {'has_next': False, 'postings': []}
        OzonOrderClient('offline', 'offline-key', transport=retry, sleep=lambda _: None).list_postings('2026-10-01', '2026-10-02')
        self.assertEqual(3, len(counts))
        for status in (401, 403):
            client, calls = self.client({'': OzonApiError('/v4/posting/fbs/list', 'offline-key', status)})
            with self.assertRaises(OzonApiError):
                client.list_postings('2026-10-01', '2026-10-02')
            self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
