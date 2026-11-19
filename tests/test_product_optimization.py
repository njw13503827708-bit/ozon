import json
import tempfile
import sqlite3
import unittest
from pathlib import Path

from backend.db.erp import database, initialize
from backend.repositories.erp import ErpRepository
from backend.repositories.analytics import AnalyticsRepository
from backend.services.product_optimization import ProductOptimizationService


class ProductOptimizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize(self.root)
        self.erp = ErpRepository(self.root)
        self.erp.upsert_store({'id': 'a', 'platform': 'OZON'})
        self.erp.upsert_store({'id': 'b', 'platform': 'OZON'})
        self.erp.upsert_product({'id': 'real-a', 'title_ru': 'Органайзер для ванной',
                                 'description_ru': 'Органайзер для ванной. Цвет: белый.'})
        self.erp.upsert_sku({'id': 'sku-a', 'product_id': 'real-a', 'seller_sku': 'item-a'})
        self.listing = self.erp.upsert_listing({'store_id': 'a', 'product_id': 'real-a',
            'sku_id': 'sku-a', 'offer_id': 'item-a', 'ozon_sku': '12345', 'listing_status': 'ONLINE',
            'remote_title': 'Органайзер  для ванной', 'remote_images': ['https://ir.ozone.ru/s3/a.jpg']})
        self.service = ProductOptimizationService(self.root)

    def test_source_is_listing_scoped_and_has_revision(self):
        source = self.service.source('a', self.listing)
        self.assertEqual('real-a', source['product_id'])
        self.assertEqual('item-a', source['offer_id'])
        self.assertEqual(['12345'], source['platform_skus'])
        self.assertEqual(64, len(source['source_revision']))
        with self.assertRaises(PermissionError):
            self.service.source('b', self.listing)
        with self.assertRaises(PermissionError):
            self.service.source('a', self.listing, organization_id='foreign')

    def test_missing_data_does_not_become_zero_or_clicks(self):
        result = self.service.diagnose(self.service.source('a', self.listing), '2026-09-26', '2026-10-02')
        self.assertIsNone(result['metrics']['hits_view']['value'])
        self.assertIsNone(result['metrics']['clicks']['value'])
        self.assertIsNone(result['metrics']['ordered_units']['value'])
        self.assertEqual('NO_DATA', result['availability'])
        self.assertIsNone(result['conversion'])

    def test_actual_zero_and_partial_daily_coverage_are_distinct(self):
        status = {metric: 'AVAILABLE' for metric in ('revenue', 'ordered_units', 'hits_view', 'session_view_pdp')}
        AnalyticsRepository(self.root).save('a', '2026-09-26', '2026-10-02', [
            {'sku': '12345', 'day': '2026-09-26', 'name': 'Органайзер',
             'revenue': '0', 'ordered_units': '0', 'hits_view': '0', 'session_view_pdp': '0'}], status)
        result = self.service.diagnose(self.service.source('a', self.listing), '2026-09-26', '2026-10-02')
        self.assertEqual('0', result['metrics']['hits_view']['value'])
        self.assertEqual('OBSERVED_ZERO', result['metrics']['hits_view']['state'])
        self.assertEqual(1, result['coverage']['recorded_sku_days'])
        self.assertEqual(7, result['coverage']['requested_sku_days'])
        self.assertEqual('INSUFFICIENT_COVERAGE', result['availability'])
        self.assertIsNone(result['metrics']['clicks']['value'])
        self.assertIsNone(result['conversion'])

    def test_other_shop_same_sku_snapshot_is_not_used(self):
        AnalyticsRepository(self.root).save('b', '2026-09-26', '2026-10-02', [
            {'sku': '12345', 'day': '2026-09-26', 'name': 'Other', 'ordered_units': '999'}], {'ordered_units': 'AVAILABLE'})
        result = self.service.diagnose(self.service.source('a', self.listing), '2026-09-26', '2026-10-02')
        self.assertIsNone(result['metrics']['ordered_units']['value'])
        self.assertEqual([], result['available_snapshots'])

    def test_unrequested_dates_have_missing_state_and_snapshot_link(self):
        AnalyticsRepository(self.root).save('a', '2026-09-26', '2026-10-02', [], {'ordered_units': 'AVAILABLE'})
        result = self.service.diagnose(self.service.source('a', self.listing), '2026-10-03', '2026-10-04')
        self.assertEqual('2026-10-03', result['query_period']['date_from'])
        self.assertEqual('2026-09-26', result['available_snapshots'][0]['date_from'])
        self.assertEqual('NO_DATA', result['availability'])

    def test_read_is_side_effect_free(self):
        with database(self.root, readonly=True) as db:
            before = list(db.iterdump())
        self.service.diagnose(self.service.source('a', self.listing), '2026-09-26', '2026-10-02')
        with database(self.root, readonly=True) as db:
            self.assertEqual(before, list(db.iterdump()))
        self.assertFalse((self.root / 'runtime/local-operations.sqlite3').exists())

    def test_unknown_attribute_id_is_not_interpreted_as_russian_fact(self):
        self.erp.persist_listing_attributes('a', [{'offer_id': 'item-a', 'attributes': [
            {'id': 999999, 'values': [{'value': '耐重1000公斤'}]}]}])
        source = self.service.source('a', self.listing)
        self.assertEqual(999999, source['attributes'][0]['id'])
        self.assertNotIn('1000', source['description_ru'])

    def test_suggestions_have_source_and_real_difference(self):
        source = self.service.source('a', self.listing)
        result = self.service.suggest(source)
        self.assertTrue(result['items'])
        self.assertTrue(all(item['source_ref'] and item['reason'] and item['uncertainty'] for item in result['items']))
        self.assertTrue(all(item['before'] != item['after'] for item in result['items']))
        self.assertTrue(any(item['field'] == 'title_ru' for item in result['items']))

    def test_hand_entered_tag_does_not_gain_suggestion_basis(self):
        source = self.service.source('a', self.listing)
        content = self.service.validate_content(source, {'title_ru': 'Органайзер', 'description_ru': '',
            'tags': ['#полка'], 'keyword_selection': [], 'rich_content': {'modules': []}}, {})
        self.assertEqual([], content['keyword_basis'])

    def test_removed_tags_lose_basis_old_and_new_tags_keep_basis(self):
        source = self.service.source('a', self.listing)
        proposal = next(item for item in self.service.suggest(source)['items'] if item['field'] == 'tags')
        tag = proposal['after']
        payload = {'title_ru': 'Органайзер', 'description_ru': '', 'tags': [tag],
            'keyword_selection': [proposal['id']], 'rich_content': {'modules': []}}
        first = self.service.validate_content(source, payload, {})
        self.assertEqual(tag, first['keyword_basis'][0]['tag'])
        payload['keyword_selection'] = []
        payload['tags'] += ['#полка']
        second = self.service.validate_content(source, payload, first)
        self.assertEqual(first['keyword_basis'], second['keyword_basis'])
        payload['tags'] = []
        self.assertEqual([], self.service.validate_content(source, payload, second)['keyword_basis'])

    def test_forged_basis_and_selection_not_in_final_tags_refused(self):
        source = self.service.source('a', self.listing)
        proposal = next(item for item in self.service.suggest(source)['items'] if item['field'] == 'tags')
        payload = {'title_ru': 'Органайзер', 'description_ru': '', 'tags': [],
            'keyword_selection': [proposal['id']], 'rich_content': {'modules': []}}
        with self.assertRaises(ValueError):
            self.service.validate_content(source, payload, {})
        with self.assertRaises(ValueError):
            self.service.validate_content(source, {**payload, 'keyword_selection': [], 'keyword_basis': [{'tag': '#полка'}]}, {})

    def test_source_change_invalidates_saved_basis(self):
        source = self.service.source('a', self.listing)
        proposal = next(item for item in self.service.suggest(source)['items'] if item['field'] == 'tags')
        payload = {'title_ru': 'Органайзер', 'description_ru': '', 'tags': [proposal['after']],
            'keyword_selection': [proposal['id']], 'rich_content': {'modules': []}}
        saved = self.service.validate_content(source, payload, {})
        changed = {**source, 'source_revision': 'changed'}
        with self.assertRaises(RuntimeError):
            self.service.validate_content(changed, {**payload, 'keyword_selection': []}, saved)

    def test_cross_product_image_and_keyword_stuffing_warned(self):
        source = self.service.source('a', self.listing)
        payload = {'title_ru': 'Органайзер', 'description_ru': '', 'tags': [], 'keyword_selection': [],
            'rich_content': {'modules': [{'title': 'Товар', 'body': '', 'image_path': 'https://ir.ozone.ru/s3/other.jpg'}]}}
        with self.assertRaises(ValueError):
            self.service.validate_content(source, payload, {})
        checks = self.service.content_checks(source, {'title_ru': 'Полка Полка Полка Полка', 'description_ru': '', 'tags': []})
        self.assertTrue(any(check['kind'] == 'STUFFING' for check in checks))

    def test_attribute_metadata_numbers_do_not_prove_new_claim(self):
        source = {**self.service.source('a', self.listing), 'attributes': [
            {'id': 9999, 'values': [{'dictionary_value_id': 8888, 'value': 'белый'}]}]}
        checks = self.service.content_checks(source, {'title_ru': 'Органайзер', 'description_ru': '9999 кг', 'tags': []})
        self.assertTrue(any(item['kind'] == 'SPEC_UNVERIFIED' for item in checks))

    def test_manual_duplicate_unrelated_tags_and_rich_spec_conflict_warned(self):
        source = self.service.source('a', self.listing)
        checks = self.service.content_checks(source, {'title_ru': 'Органайзер', 'description_ru': 'Цвет: белый.',
            'tags': ['#телефон', '#телефон'], 'rich_content': {'modules': [{'title': 'Органайзер', 'body': 'Объём: 99 л', 'image_path': ''}]}})
        self.assertTrue(any(item['kind'] == 'TAG_DUPLICATE' for item in checks))
        self.assertTrue(any(item['kind'] == 'TAG_UNRELATED' for item in checks))
        self.assertTrue(any(item['kind'] == 'SPEC_UNVERIFIED' for item in checks))

    def test_source_product_ownership_and_malformed_attribute_rows_refused(self):
        with database(self.root) as db:
            db.execute("INSERT INTO organizations VALUES('foreign','Other','now')")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE products SET organization_id='foreign' WHERE id='real-a'")
        self.erp.persist_listing_attributes('a', [{'offer_id': 'item-a', 'attributes': ['malformed']}])
        with self.assertRaises(ValueError):
            self.service.source('a', self.listing)

    def test_candidate_filters_use_evidence_and_do_not_cross_stores(self):
        result = self.service.candidates('a', '2026-09-26', '2026-10-02', issue='CONTENT_MISSING')
        self.assertEqual(1, result['total'])
        self.assertEqual(self.listing, result['items'][0]['listing_id'])
        self.assertIn('未提供', result['items'][0]['reasons'][0])
        self.assertEqual(0, self.service.candidates('a', '2026-09-26', '2026-10-02', issue='OBSERVED_ZERO')['total'])
        self.assertEqual(0, self.service.candidates('b', '2026-09-26', '2026-10-02')['total'])

    def test_generic_units_and_field_headings_are_not_filler_keywords(self):
        source = {**self.service.source('a', self.listing), 'title_ru': '24 шт. Органайзер для ванной',
                  'description_ru': 'Цвет: белый. Количество: 1 шт.'}
        tags = {item['after'] for item in self.service.suggest(source)['items'] if item['field'] == 'tags'}
        self.assertNotIn('#шт', tags)
        self.assertNotIn('#количествошт', tags)
        self.assertNotIn('#цветбелый', tags)

    def test_duplicate_daily_rows_never_inflate_metrics(self):
        row = {'sku': '12345', 'day': '2026-09-26', 'name': 'Органайзер', 'hits_view': '12'}
        AnalyticsRepository(self.root).save('a', '2026-09-26', '2026-10-02', [row, row], {'hits_view': 'AVAILABLE'})
        diagnosis = self.service.diagnose(self.service.source('a', self.listing), '2026-09-26', '2026-10-02')
        self.assertIsNone(diagnosis['metrics']['hits_view']['value'])
        self.assertEqual('INVALID_DATA', diagnosis['availability'])


if __name__ == '__main__':
    unittest.main()
