import tempfile
import unittest
from pathlib import Path

from backend.db.erp import initialize
from backend.repositories.erp import ErpRepository
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.product_optimization import ProductOptimizationService
from backend.services.listing_reads import ListingReadService
from backend.integrations.ozon.analytics_client import OzonAnalyticsClient
from backend.integrations.ozon.base_client import OzonApiError


class ListingReadsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize(self.root)
        repo = ErpRepository(self.root)
        repo.upsert_store({'id': 'a', 'platform': 'OZON'})
        repo.upsert_product({'id': 'own', 'title_ru': 'Органайзер для ванной'})
        self.id = repo.upsert_listing({'store_id': 'a', 'product_id': 'own', 'offer_id': 'own',
            'ozon_sku': '12345', 'remote_title': 'Органайзер для ванной', 'listing_status': 'ONLINE'})
        LocalOperationsRepository(self.root).initialize()
        self.source = ProductOptimizationService(self.root).source('a', self.id)

    def test_search_contract_preserves_actual_period_and_never_invents_clicks(self):
        calls = []
        def transport(endpoint, payload):
            calls.append((endpoint, payload))
            base = {'analytics_period': {'date_from': '2026-09-26', 'date_to': '2026-10-02'}, 'page_count': 1, 'total': 1}
            if endpoint.endswith('/details'):
                return {**base, 'queries': [{'sku': 12345, 'query': 'органайзер для ванной', 'unique_search_users': 0, 'unique_view_users': None, 'position': None, 'order_count': 0}]}
            return {**base, 'items': [{'sku': 12345, 'unique_search_users': 0, 'unique_view_users': None}]}
        service = ListingReadService(self.root, 'offline', 'offline-key', transport=transport)
        snapshot = service.search(self.source, '2026-09-26', '2026-10-02')
        self.assertEqual(15, calls[1][1]['limit_by_sku'])
        self.assertEqual(['12345'], calls[0][1]['skus'])
        self.assertEqual(0, snapshot['data']['rows'][0]['unique_search_users'])
        self.assertIsNone(snapshot['data']['rows'][0]['unique_view_users'])
        self.assertNotIn('clicks', snapshot['data']['rows'][0])
        self.assertEqual('TOP15_PER_SKU', snapshot['data']['completeness'])
        suggestions = ProductOptimizationService(self.root).suggest(self.source, snapshot)
        self.assertTrue(any(item['basis'].startswith('店铺实测词') for item in suggestions['items']))

    def test_wrong_sku_or_period_and_partial_failure_never_saved(self):
        for returned_sku, returned_end in ((99999, '2026-10-02'), (12345, '2026-10-03')):
            def transport(endpoint, payload):
                return {'analytics_period': {'date_from': '2026-09-26', 'date_to': returned_end}, 'page_count': 1, 'total': 1,
                    'items': [{'sku': returned_sku}], 'queries': [{'sku': returned_sku, 'query': 'органайзер'}]}
            with self.assertRaises(ValueError):
                ListingReadService(self.root, 'offline', 'offline-key', transport=transport).search(self.source, '2026-09-26', '2026-10-02')
        self.assertIsNone(LocalOperationsRepository(self.root).snapshot(f'SEARCH:{self.id}', 'a', '2026-09-26', '2026-10-02'))

    def test_analysis_request_is_sku_limited_and_missing_records_stay_unknown(self):
        calls = []
        def transport(endpoint, payload):
            calls.append(payload)
            return {'result': {'data': []}}
        ListingReadService(self.root, 'offline', 'offline-key', transport=transport).performance(self.source, '2026-09-26', '2026-10-02')
        self.assertEqual([{'key': 'sku', 'op': 'EQ', 'value': '12345'}], calls[0]['filters'])
        diagnosis = ProductOptimizationService(self.root).diagnose(self.source, '2026-09-26', '2026-10-02')
        self.assertIsNone(diagnosis['metrics']['hits_view']['value'])
        self.assertEqual('NO_DATA', diagnosis['availability'])

    def test_search_permission_denial_has_one_attempt_and_no_cache(self):
        calls = []
        def transport(endpoint, payload):
            calls.append(endpoint)
            raise OzonApiError(endpoint, 'offline-key', 403)
        with self.assertRaises(OzonApiError):
            ListingReadService(self.root, 'offline', 'offline-key', transport=transport).search(self.source, '2026-09-26', '2026-10-02')
        self.assertEqual(1, len(calls))

    def test_unverified_search_snapshot_not_presented_as_measured(self):
        fake = {'store_id': 'a', 'id': 'provided', 'date_from': '2026-09-26', 'date_to': '2026-10-02',
                'data': {'source': 'MANUAL_PROVIDED', 'rows': [{'sku': '12345', 'query': 'органайзер для ванной'}]}}
        suggestions = ProductOptimizationService(self.root).suggest(self.source, fake)
        self.assertFalse(any(item['basis'].startswith('店铺实测词') for item in suggestions['items']))

    def test_title_edit_and_new_observation_period_keep_valid_old_basis(self):
        snapshot = {'store_id': 'a', 'id': 'placeholder', 'date_from': '2026-09-26', 'date_to': '2026-10-02',
            'data': {'source': 'OZON_SELLER_API/v1/analytics/product-queries/details', 'rows': [
                {'sku': '12345', 'query': 'органайзер для ванной', 'unique_search_users': 8}]}}
        repo = LocalOperationsRepository(self.root)
        repo.save_snapshot(f'SEARCH:{self.id}', 'a', snapshot['date_from'], snapshot['date_to'], snapshot['data'])
        snapshot = repo.snapshot(f'SEARCH:{self.id}', 'a', snapshot['date_from'], snapshot['date_to'])
        service = ProductOptimizationService(self.root)
        proposal = next(item for item in service.suggest(self.source, snapshot)['items'] if item['field'] == 'tags')
        payload = {'title_ru': 'Органайзер', 'description_ru': '', 'tags': [proposal['after']],
            'keyword_selection': [proposal['id']], 'rich_content': {'modules': []},
            'baseline_period': {'date_from': '2026-09-26', 'date_to': '2026-10-02'}}
        first = service.validate_content(self.source, payload, {}, snapshot)
        edited = {**payload, 'title_ru': 'Органайзер для ванной', 'keyword_selection': [],
            'baseline_period': {'date_from': '2026-10-03', 'date_to': '2026-10-04'}}
        next_content = service.validate_content(self.source, edited, first)
        self.assertEqual(first['keyword_basis'], next_content['keyword_basis'])
        repo.save_snapshot(f'SEARCH:{self.id}', 'a', '2026-09-26', '2026-10-02', {
            **snapshot['data'], 'rows': [{'sku': '12345', 'query': 'органайзер для ванной', 'unique_search_users': 99}]})
        with self.assertRaises(RuntimeError):
            service.validate_content(self.source, edited, first)


if __name__ == '__main__':
    unittest.main()
