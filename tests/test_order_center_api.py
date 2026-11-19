import asyncio
import json
import os
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

from fastapi import FastAPI
from backend.api import orders
from tests import test_order_center as fixtures
from backend.services.order_sync_service import OrderSyncService


class OrderCenterApiTest(unittest.TestCase):
    def setUp(self):
        fixtures.OrderCenterTest.setUp(self)
        self.app = FastAPI()
        self.app.include_router(orders.router)
        self.scope = {'operator': {'id': 'host', 'role': 'owner', 'is_host_device': True}}

    def request(self, url, method='GET', body=None, state=None):
        parsed = urlsplit(url)
        async def run():
            messages = []
            async def receive():
                return {'type': 'http.request', 'body': json.dumps(body or {}).encode(), 'more_body': False}
            async def send(message):
                messages.append(message)
            await self.app({'type': 'http', 'method': method, 'path': parsed.path, 'query_string': parsed.query.encode(),
                'headers': [(b'content-type', b'application/json')], 'state': self.scope if state is None else state,
                'scheme': 'http', 'server': ('test', 80), 'client': ('127.0.0.1', 1)}, receive, send)
            status = next(item['status'] for item in messages if item['type'] == 'http.response.start')
            return status, json.loads(b''.join(item.get('body', b'') for item in messages if item['type'] == 'http.response.body'))
        with patch.object(orders, 'ROOT', self.root):
            return asyncio.run(run())

    def test_reads_without_credentials_or_live_calls_and_invalid_filters_refused(self):
        with patch.object(orders, '_store_credentials') as credentials:
            status, data = self.request('/api/erp/stores/a/orders?date_from=2026-10-01&date_to=2026-10-02')
            self.assertEqual(200, status)
            self.assertEqual('NOT_SYNCED', data['availability'])
            self.assertFalse(data['read_authorized'])
            self.assertEqual(422, self.request('/api/erp/stores/a/orders?date_from=x&date_to=x')[0])
            credentials.assert_not_called()

    def test_host_and_explicit_read_authorization_precede_credentials(self):
        with patch.object(orders, '_store_credentials') as credentials:
            body = {'date_from': '2026-10-01', 'date_to': '2026-10-02'}
            self.assertEqual(403, self.request('/api/erp/stores/a/orders/sync', 'POST', body)[0])
            with patch.dict(os.environ, {'CAF_ORDER_READ_APPROVED': '1'}):
                self.assertEqual(403, self.request('/api/erp/stores/a/orders/sync', 'POST', body, state={})[0])
                self.assertEqual(403, self.request('/api/erp/stores/a/orders/sync', 'POST', body, state={'operator': {'is_host_device': True, 'role': 'viewer'}})[0])
            credentials.assert_not_called()

    def test_detail_and_job_cross_store_are_rejected(self):
        client, _ = fixtures.OrderCenterTest.client(self, {'': {'has_next': False, 'postings': [fixtures.posting()]}})
        OrderSyncService(self.root, client).run('a', '2026-10-01', '2026-10-02')
        url = '/api/erp/stores/a/orders/100-1?date_from=2026-10-01&date_to=2026-10-02'
        self.assertEqual(200, self.request(url)[0])
        self.assertEqual(404, self.request(url.replace('/stores/a/', '/stores/b/'))[0])
        job, _ = self.repo.start_job('ORDERS', 'a', '2026-10-01:2026-10-02')
        self.assertEqual(403, self.request(f"/api/erp/stores/b/orders/jobs/{job['id']}")[0])
        self.assertEqual(403, self.request(url, state={'operator': {'organization_id': 'foreign'}})[0])

    def test_changed_credentials_must_be_revalidated_before_external_read(self):
        with patch.dict(os.environ, {'CAF_ORDER_READ_APPROVED': '1'}), patch.object(orders, '_store_credentials', return_value=({'validation_status': 'unverified'}, {})):
            self.assertEqual(409, self.request('/api/erp/stores/a/orders/sync', 'POST', {
                'date_from': '2026-10-01', 'date_to': '2026-10-02'})[0])


if __name__ == '__main__':
    unittest.main()
