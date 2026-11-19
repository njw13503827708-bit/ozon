import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.integrations.ozon.credential_store import CredentialStore
from scripts.workbench_stores import upsert_store, list_stores, read_secret, load_registry


class StoreDisplayNameTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, name, **extra):
        return upsert_store(self.root, {'display_name': name, 'client_id': 'fixture-client',
                                       'api_key': 'fixture-only-not-real', **extra})

    def test_chinese_and_russian_names_save_and_reload(self):
        for name in ['豫鑫汇', 'Добрый Товар']:
            item = self.create(name)
            self.assertRegex(item['id'], r'^[a-z0-9_-]{1,48}$')
            self.assertNotEqual(item['id'], name)
            self.assertEqual(next(x for x in list_stores(self.root) if x['id'] == item['id'])['display_name'], name)
            self.assertNotIn('api_key', item)

    def test_same_display_name_never_overwrites_another_store(self):
        first = self.create('同名店铺')
        second = self.create('同名店铺')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(list_stores(self.root)), 2)

    def test_rename_retains_existing_id_and_credentials(self):
        original = self.create('Existing', id='existing-store')
        shop = load_registry(self.root)['shops'][0]
        secret = read_secret(self.root, shop)
        result = upsert_store(self.root, {'display_name': '中文新名称'}, store_id=original['id'])
        self.assertEqual(result['id'], 'existing-store')
        self.assertEqual(read_secret(self.root, load_registry(self.root)['shops'][0]), secret)

    def test_missing_display_name_is_rejected_without_registry_write(self):
        with self.assertRaises(ValueError):
            self.create('  ')
        self.assertFalse((self.root / 'ozon-adapter/shops.json').exists())

    def test_store_listing_and_local_edit_do_not_rewrite_existing_credential_acl(self):
        original = self.create('Existing', id='existing-store')
        secret_file = self.root / 'ozon-adapter/.env.existing-store'
        before = secret_file.read_bytes()
        with patch.object(CredentialStore, '_secure', side_effect=OSError('ACL denied')):
            self.assertEqual(original['id'], list_stores(self.root)[0]['id'])
            edited = upsert_store(self.root, {'display_name': 'Existing renamed'}, store_id=original['id'])
        self.assertEqual('Existing renamed', edited['display_name'])
        self.assertEqual(before, secret_file.read_bytes())
