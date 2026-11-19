import asyncio
import io
import json
import os
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import FastAPI
from backend.api import optimization
from backend.repositories.local_operations import LocalOperationsRepository
from tests import test_product_optimization as fixtures


class OptimizationApiTest(unittest.TestCase):
    def setUp(self):
        fixtures.ProductOptimizationTest.setUp(self)
        LocalOperationsRepository(self.root).initialize()
        self.app = FastAPI()
        self.app.include_router(optimization.router)
        self.path = f'/api/erp/stores/a/listings/{self.listing}/optimization'
        self.scope = {'operator': {'id': 'local-host', 'role': 'owner', 'is_host_device': True}}

    def request(self, path, method='GET', body=None, state=None):
        parsed = urlsplit(path)
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
            data = b''.join(item.get('body', b'') for item in messages if item['type'] == 'http.response.body')
            return status, data
        with patch.object(optimization, 'ROOT', self.root):
            return asyncio.run(run())

    def payload(self, expected=0):
        return {'expected_version': expected, 'source_revision': self.service.source('a', self.listing)['source_revision'],
            'content': {'title_ru': 'Органайзер для ванной', 'description_ru': 'Цвет: белый.', 'tags': [],
                'keyword_selection': [], 'rich_content': {'modules': [{'title': 'Органайзер', 'body': 'Цвет: белый.', 'image_path': ''}]}}}

    def test_generation_routes_scope_and_host_gate_without_inline_model_calls(self):
        body = {'expected_version':0, 'source_revision':self.payload()['source_revision'],
                'fields':['title_ru','description_ru'], 'period':{'date_from':'2026-09-26','date_to':'2026-10-02'}}
        with patch.object(optimization, 'run_optimization_task') as thread:
            status, raw = self.request(self.path+'/generate', 'POST', body)
            self.assertEqual(202, status)
            job = json.loads(raw)
            self.assertEqual(200, self.request(self.path+'/tasks/'+job['id'])[0])
            self.assertEqual(403, self.request(self.path+'/generate', 'POST', body, {'operator':{'is_host_device':False}})[0])
            foreign = self.path.replace('/stores/a/', '/stores/b/')+'/tasks/'+job['id']
            self.assertEqual(403, self.request(foreign)[0])
            self.assertEqual(200, self.request(self.path+'/tasks/'+job['id']+'/cancel', 'POST')[0])
            self.assertEqual(202, self.request(self.path+'/generate', 'POST', body)[0])
            self.assertEqual(1, thread.call_count)

    def test_generation_accepts_only_a_configured_text_model_choice(self):
        from backend.integrations.ai.provider_config import configure
        from backend.services.optimization_agent import OptimizationAgent
        configure(self.root, {'base_url':'https://ai.opendoor.sbs', 'model':'text-a',
                              'models':['text-a','text-b','text-c'], 'api_key':'offline-test-key'})
        body = {'expected_version':0, 'source_revision':self.payload()['source_revision'],
                'fields':['title_ru'], 'period':{'date_from':'2026-09-26','date_to':'2026-10-02'}, 'model':'text-b'}
        with patch.object(optimization, 'run_optimization_task'):
            status, raw = self.request(self.path+'/generate', 'POST', body)
        self.assertEqual(202, status)
        task = OptimizationAgent(self.root).view('a', self.listing, json.loads(raw)['id'])
        self.assertEqual('text-b', task['context']['model'])
        self.assertEqual(422, self.request(self.path+'/generate', 'POST', {**body, 'model':'not-configured'})[0])

    def test_publication_and_asset_mutations_reject_nonhost_and_foreign_org(self):
        for state in ({'operator':{'is_host_device':False}},
                      {'operator':{'is_host_device':True,'role':'viewer'}},
                      {'operator':{'organization_id':'foreign','is_host_device':True,'role':'owner'}}):
            for path,body in (('/api/erp/stores/optimization-publication/review',{'targets':[]}),
                (self.path+'/assets/review',{'reference':'asset://fake','identity_and_rights_confirmed':True})):
                with self.subTest(path=path,state=state):self.assertEqual(403,self.request(path,'POST',body,state)[0])

    def test_unverified_specification_is_not_platform_ready_after_local_review(self):
        body = self.payload(); body['content']['description_ru'] = 'Объём: 999 л'
        self.assertEqual(201, self.request(self.path, 'POST', body)[0])
        confirm = {'source_revision':body['source_revision'], 'facts_confirmed':True,
                   'images_authorized':True, 'warnings_reviewed':True}
        status, raw = self.request(self.path+'/versions/1/review', 'POST', confirm)
        self.assertEqual(200, status)
        self.assertFalse(json.loads(raw)['review']['confirmation']['platform_ready'])

    def test_save_reopen_review_export_and_restore_real_listing(self):
        self.assertEqual(200, self.request(self.path)[0])
        self.assertEqual(201, self.request(self.path, 'POST', self.payload())[0])
        reopened = json.loads(self.request(self.path)[1])
        self.assertEqual('Цвет: белый.', reopened['versions'][0]['content']['description_ru'])
        self.assertEqual(409, self.request(self.path + '/versions/1/export')[0])
        confirmation = {'source_revision': self.payload()['source_revision'], 'facts_confirmed': True,
                        'images_authorized': True, 'warnings_reviewed': True}
        self.assertEqual(200, self.request(self.path + '/versions/1/review', 'POST', confirmation)[0])
        status, bundle = self.request(self.path + '/versions/1/export')
        self.assertEqual(200, status)
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIn('README.txt', archive.namelist())
            self.assertEqual('Цвет: белый.', archive.read('description-ru.txt').decode('utf-8'))
            self.assertIn('not uploaded', archive.read('README.txt').decode('utf-8'))
        self.assertEqual(201, self.request(self.path + '/restore', 'POST', {
            'version': 1, 'expected_version': 1, 'source_revision': confirmation['source_revision']})[0])
        versions = json.loads(self.request(self.path)[1])['versions']
        self.assertEqual([2, 1], [item['version'] for item in versions])
        self.assertEqual('DRAFT', versions[0]['status'])
        self.assertEqual(200, self.request(self.path + '/versions/2/review', 'POST', confirmation)[0])
        self.assertEqual(201, self.request(self.path + '/restore', 'POST', {
            'version': 2, 'expected_version': 2, 'source_revision': confirmation['source_revision']})[0])

    def test_generated_version_is_visible_after_workbench_reopens(self):
        from backend.services.optimization_agent import OptimizationAgent
        from tests.test_ai_content_client import model_output

        generated = model_output()
        generated['provider_evidence'] = {'response_id': 'resp_offline_test', 'usage': {'output_tokens': 12}}
        from backend.integrations.ai.provider_config import configure
        configure(self.root, {'base_url': 'https://ai.opendoor.sbs', 'model': 'offline-test-model',
                              'api_key': 'offline-test-key'})
        source = self.service.source('a', self.listing)
        job = OptimizationAgent(self.root).start('a', self.listing, 0, source['source_revision'],
            ['title_ru', 'description_ru', 'tags', 'rich_content'],
            {'date_from': '2026-09-26', 'date_to': '2026-10-02'}, 'local-host')
        with patch('backend.services.optimization_agent.generate_content', return_value=generated):
            result = OptimizationAgent(self.root).run(job['id'])
        self.assertEqual('REVIEW_REQUIRED', result['status'])
        reopened = json.loads(self.request(self.path)[1])
        self.assertEqual(1, reopened['versions'][0]['version'])
        self.assertEqual('BACKEND_MODEL_API', reopened['versions'][0]['content']['generation_source'])
        self.assertEqual('resp_offline_test', reopened['versions'][0]['content']['provider_evidence']['response_id'])
        self.assertNotEqual(source['title_ru'], reopened['versions'][0]['content']['title_ru'])
        self.assertEqual(reopened['versions'][0]['content'], json.loads(self.request(self.path)[1])['versions'][0]['content'])

    def test_permissions_and_cross_store_rejection(self):
        self.assertEqual(403, self.request(self.path, 'POST', self.payload(), state={})[0])
        self.assertEqual(403, self.request(self.path, 'POST', self.payload(), state={
            'operator': {'is_host_device': True, 'role': 'viewer'}})[0])
        self.assertEqual(403, self.request(self.path.replace('/stores/a/', '/stores/b/'))[0])
        self.assertEqual(403, self.request(self.path, state={'operator': {'organization_id': 'foreign'}})[0])
        self.assertEqual([], LocalOperationsRepository(self.root).versions('a', self.listing))

    def test_stale_version_and_source_preserve_saved_contents(self):
        self.assertEqual(201, self.request(self.path, 'POST', self.payload())[0])
        self.assertEqual(409, self.request(self.path, 'POST', self.payload())[0])
        payload = self.payload(1)
        payload['source_revision'] = '0' * 64
        self.assertEqual(409, self.request(self.path, 'POST', payload)[0])
        self.assertEqual(1, len(LocalOperationsRepository(self.root).versions('a', self.listing)))

    def test_html_export_escapes_user_text_and_does_not_fetch_images(self):
        payload = self.payload()
        payload['content']['rich_content']['modules'][0]['body'] = '<script>alert(1)</script>'
        self.assertEqual(201, self.request(self.path, 'POST', payload)[0])
        self.assertEqual(200, self.request(self.path + '/versions/1/review', 'POST', {
            'source_revision': payload['source_revision'], 'facts_confirmed': True,
            'images_authorized': True, 'warnings_reviewed': True})[0])
        status, data = self.request(self.path + '/versions/1/export')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            html = archive.read('preview.html').decode('utf-8')
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)

    def test_saved_source_change_visible_and_export_refused(self):
        payload = self.payload()
        self.request(self.path, 'POST', payload)
        self.request(self.path + '/versions/1/review', 'POST', {'source_revision': payload['source_revision'],
            'facts_confirmed': True, 'images_authorized': True, 'warnings_reviewed': True})
        self.erp.persist_listing_attributes('a', [{'offer_id': 'item-a', 'attributes': [
            {'id': 4191, 'values': [{'value': 'Новое описание.'}]}]}])
        data = json.loads(self.request(self.path)[1])
        self.assertFalse(data['versions'][0]['source_valid'])
        self.assertEqual(409, self.request(self.path + '/versions/1/export')[0])

    def test_default_period_works_without_windows_timezone_package(self):
        with patch.object(optimization, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 2, 1, tzinfo=timezone(timedelta(hours=3)))
            self.assertEqual(('2026-09-25', '2026-10-01'), optimization.period('', ''))

    def test_preview_checks_do_not_save_and_storage_failure_preserves_data(self):
        payload = self.payload()
        payload['content']['description_ru'] = 'Нагрузка: 999 кг'
        status, data = self.request(self.path + '/check', 'POST', payload)
        self.assertEqual(200, status)
        self.assertTrue(any(item['kind'] == 'SPEC_UNVERIFIED' for item in json.loads(data)['checks']))
        self.assertEqual([], LocalOperationsRepository(self.root).versions('a', self.listing))
        import sqlite3
        with patch.object(LocalOperationsRepository, 'save_version', side_effect=sqlite3.OperationalError('offline disk error')):
            self.assertEqual(503, self.request(self.path, 'POST', payload)[0])
        self.assertEqual([], LocalOperationsRepository(self.root).versions('a', self.listing))

    def test_new_reads_require_explicit_permission_and_connected_credentials(self):
        body = {'kind': 'SEARCH', 'date_from': '2026-09-26', 'date_to': '2026-10-02'}
        with patch.object(optimization, '_store_credentials') as credentials:
            self.assertEqual(403, self.request(self.path + '/read-data', 'POST', body)[0])
            credentials.assert_not_called()
        with patch.dict(os.environ, {'CAF_ENHANCED_READ_APPROVED': '1'}), patch.object(optimization, '_store_credentials', return_value=({'validation_status': 'unverified'}, {})):
            self.assertEqual(409, self.request(self.path + '/read-data', 'POST', body)[0])

    def test_read_job_scoped_and_permission_failure_safe(self):
        body = {'kind': 'SEARCH', 'date_from': '2026-09-26', 'date_to': '2026-10-02'}
        from backend.integrations.ozon.base_client import OzonApiError
        with patch.dict(os.environ, {'CAF_ENHANCED_READ_APPROVED': '1'}), patch.object(optimization, '_store_credentials', return_value=({'validation_status': 'connected'}, {'client_id': 'OFFLINE', 'api_key': 'OFFLINE-SECRET'})), patch.object(optimization, 'ListingReadService') as service, patch.object(optimization, 'threading') as threads:
            service.return_value.search.side_effect = OzonApiError('/v1/analytics/product-queries', 'OFFLINE-SECRET', 403)
            status, data = self.request(self.path + '/read-data', 'POST', body)
            self.assertEqual(202, status)
            with patch.object(optimization, 'ROOT', self.root):
                threads.Thread.call_args.kwargs['target']()
            job = LocalOperationsRepository(self.root).job(json.loads(data)['job_id'])
            self.assertEqual('FAILED', job['status'])
            self.assertNotIn('OFFLINE-SECRET', job['message'])
            self.assertEqual(403, self.request(self.path.replace('/stores/a/', '/stores/b/') + '/read-jobs/' + job['id'])[0])

    def test_search_actual_partial_period_is_visible(self):
        LocalOperationsRepository(self.root).save_snapshot(f'SEARCH:{self.listing}', 'a', '2026-10-01', '2026-10-02', {
            'source': 'OZON_SELLER_API/v1/analytics/product-queries/details', 'rows': []})
        status, data = self.request(self.path + '?date_from=2026-09-26&date_to=2026-10-02')
        self.assertEqual(200, status)
        self.assertEqual('2026-10-01', json.loads(data)['search_snapshot']['date_from'])

    def test_source_database_version_failure_is_unavailable_not_conflict(self):
        from backend.db.erp import database
        with database(self.root) as db:
            db.execute("DELETE FROM schema_migrations WHERE version='014'")
        self.assertEqual(503, self.request(self.path)[0])


if __name__ == '__main__':
    unittest.main()
