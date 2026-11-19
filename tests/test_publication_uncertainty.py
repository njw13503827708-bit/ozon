import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'ozon-uploader'), str(ROOT / 'ozon-adapter')]
from ozon_uploader import service
from ozon_uploader.client import OzonUploadApiError


class PublicationUncertaintyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.product = Path(self.temp.name) / 'products/P000101'
        (self.product / 'output').mkdir(parents=True)
        self.items = [{'offer_id': 'owned-offer', 'name': 'Полка'}]
        self.client = Mock()
        mode = patch.dict(os.environ, {'UPLOAD_MODE': 'production'})
        mode.start()
        self.addCleanup(mode.stop)

    def test_unknown_intent_blocks_before_image_checks_or_platform_reads(self):
        path = self.product / 'output/ozon-write-intent.json'
        path.write_text(json.dumps({'state': 'RESULT_UNKNOWN', 'request_hash': 'old'}))
        with patch.dict(os.environ, {'UPLOAD_MODE': 'production'}), patch.object(service, 'current_upload_image_gate', side_effect=AssertionError('Should stop before gates')):
            with self.assertRaisesRegex(service.UploadGateError, '结果.*确认'):
                service.execute_upload(self.product, self.client)
        self.client.get_products_info.assert_not_called()
        self.client.import_products.assert_not_called()

    def test_intent_is_durable_before_write_and_duplicate_is_refused(self):
        def submit(items):
            intent = json.loads((self.product / 'output/ozon-write-intent.json').read_text(encoding='utf-8'))
            self.assertEqual('DISPATCHING', intent['state'])
            self.assertEqual(['owned-offer'], intent['offer_ids'])
            return {'result': {'task_id': 41}}
        self.client.import_products.side_effect = submit
        response = service._submit_import_once(self.product, self.client, self.items, 'create')
        self.assertEqual(41, response['result']['task_id'])
        with self.assertRaises(service.UploadGateError):
            service._submit_import_once(self.product, self.client, self.items, 'create')
        self.assertEqual(1, self.client.import_products.call_count)

    def test_timeout_is_unknown_and_never_automatically_replayed(self):
        self.client.import_products.side_effect = OzonUploadApiError('/v3/product/import', 'network interrupted')
        with self.assertRaises(OzonUploadApiError):
            service._submit_import_once(self.product, self.client, self.items, 'update')
        intent = json.loads((self.product / 'output/ozon-write-intent.json').read_text(encoding='utf-8'))
        self.assertEqual('RESULT_UNKNOWN', intent['state'])
        with self.assertRaises(service.UploadGateError):
            service._submit_import_once(self.product, self.client, self.items, 'update')
        self.assertEqual(1, self.client.import_products.call_count)

    def test_update_target_ids_must_match_and_cannot_be_missing(self):
        service._assert_update_targets({'items': [{'offer_id': 'owned-offer', 'id': 100}]}, {'owned-offer': '100'})
        for response in ({'items': []}, {'items': [{'offer_id': 'owned-offer', 'id': 200}]}):
            with self.assertRaises(service.UploadGateError):
                service._assert_update_targets(response, {'owned-offer': '100'})


if __name__ == '__main__':
    unittest.main()
