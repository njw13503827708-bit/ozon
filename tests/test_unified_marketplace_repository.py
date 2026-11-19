from backend.db.erp import initialize as initialize_erp_test_database
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.db.erp import database
from backend.repositories.erp import ErpRepository


class UnifiedMarketplaceRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        initialize_erp_test_database(self.root)
        self.repo = ErpRepository(self.root)
        self.repo.upsert_store({'id':'s','platform':'OZON'})
        self.repo.upsert_store({'id':'other','platform':'KASPI'})
        self.repo.upsert_product({'id':'p'})
        self.repo.upsert_sku({'id':'sku','product_id':'p'})

    def listing(self, **changes):
        return dict(store_id='s', product_id='p', sku_id='sku', platform='OZON', offer_id='offer', **changes)

    def test_account_store_channel_and_unknown_identity(self):
        self.repo.upsert_marketplace_account({'id':'a','platform':'OZON','credential_ref':'existing-ref'})
        self.repo.upsert_store({'id':'s','account_id':'a','client_id':'not-seller','category_id':'not-seller'})
        self.repo.upsert_marketplace_channel({'id':'c','store_id':'s','display_name':'FBS'})
        self.assertIsNone(self.repo.get_marketplace_account('a')['external_id'])
        self.assertIsNone(self.repo.get_store('s')['external_id'])
        self.assertEqual('a', self.repo.get_store('s')['account_id'])
        self.assertEqual('OZON',self.repo.list_marketplace_channels('s')[0]['platform'])
        self.assertEqual('default',self.repo.list_marketplace_accounts()[0]['organization_id'])
        self.repo.upsert_store({'id':'s','display_name':'Renamed'})
        self.assertEqual('a', self.repo.get_store('s')['account_id'])
        with self.assertRaises((ValueError,sqlite3.IntegrityError)):
            self.repo.upsert_store({'id':'other','platform':'KASPI','account_id':'a'})

    def test_canonical_identity_multi_store_listing_uniqueness_and_nulls(self):
        first = self.repo.upsert_listing(self.listing(external_id='remote'))
        self.assertEqual(first, self.repo.upsert_listing(self.listing(external_id='remote')))
        self.repo.upsert_listing({'store_id':'other','platform':'KASPI','product_id':'p','sku_id':'sku','offer_id':'other-offer','external_id':'remote'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.upsert_listing({**self.listing(external_id='remote'),'offer_id':'duplicate'})
        for offer in ('unknown-1','unknown-2'):
            self.repo.upsert_listing({**self.listing(),'offer_id':offer})
        product = self.repo.get_product('p')
        self.assertEqual('default',product['organization_id'])
        self.assertEqual('default',product['skus'][0]['organization_id'])
        self.assertEqual(4,len(product['listings']))
        self.assertNotIn('store_id',product)
        self.assertNotIn('external_id',product)
        self.assertNotEqual(first,product['id'])
        self.assertNotEqual(first,product['skus'][0]['id'])

    def test_db_rejects_cross_scope_channel_sku_and_unknown_organization(self):
        self.repo.upsert_marketplace_channel({'id':'other-channel','store_id':'other'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.upsert_listing(self.listing(channel_id='other-channel'))
        self.repo.upsert_product({'id':'p2'})
        self.repo.upsert_sku({'id':'sku2','product_id':'p2'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.upsert_listing({**self.listing(),'sku_id':'sku2'})
        with database(self.root) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE products SET organization_id='missing' WHERE id='p'")

    def test_cursor_jobs_and_audit_context_survive_reopen(self):
        self.repo.upsert_sync_cursor('s','review', 'opaque:+/==', last_success_at='2026-09-25')
        self.repo.upsert_sync_cursor('s','review', 'next', last_success_at='2026-09-26')
        self.repo.upsert_sync_cursor('other','review', None)
        reopened = ErpRepository(self.root)
        cursor = reopened.get_sync_cursor('s','review')
        self.assertEqual(('default','OZON','next','2026-09-26'),tuple(cursor[k] for k in ('organization_id','platform','cursor','last_success_at')))
        self.assertIsNone(reopened.get_sync_cursor('s','product'))
        for job_id in (self.repo.create_sync_job('READ','s'),self.repo.create_or_get_active_sync_job('READ2','s')[0]):
            job = self.repo.get_sync_job(job_id)
            self.assertEqual(('default','OZON','s'),tuple(job[k] for k in ('organization_id','platform','store_id')))
        self.repo.add_audit_log('READ','store','s',{})
        self.repo.add_audit_log('LOCAL','product','p',{})
        self.repo.add_audit_log('READ','listing','1',{},store_id='s')
        self.assertEqual('OZON',self.repo.list_audit_logs('s')[0]['platform'])
        self.assertIsNone(self.repo.list_audit_logs('p')[0]['store_id'])
        self.assertEqual('s',self.repo.list_audit_logs('1')[0]['store_id'])

    def test_db_parent_changes_cannot_orphan_scope(self):
        self.repo.upsert_listing(self.listing())
        with database(self.root) as db:
            for statement in ("DELETE FROM organizations WHERE id='default'",
                              "UPDATE organizations SET id='changed' WHERE id='default'",
                              "UPDATE stores SET platform='KASPI' WHERE id='s'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)

    def test_channel_remote_ids_are_store_scoped_and_accounts_have_no_secrets(self):
        self.repo.upsert_marketplace_channel({'id':'c','store_id':'s','external_id':'remote'})
        self.repo.upsert_marketplace_channel({'id':'c2','store_id':'other','external_id':'remote'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.upsert_marketplace_channel({'id':'c3','store_id':'s','external_id':'remote'})
        self.repo.upsert_marketplace_account({'id':'a','platform':'OZON','client_id':'not-stored','api_key':'not-stored'})
        account = self.repo.get_marketplace_account('a')
        self.assertNotIn('api_key',account)
        self.assertNotIn('client_id',account)
        self.assertIsNone(account['external_id'])
        for method,item in ((self.repo.upsert_marketplace_account,{'id':'bad','platform':'OZON'}),
                            (self.repo.upsert_marketplace_channel,{'id':'bad','store_id':'s'})):
            with self.assertRaises(ValueError):
                method({**item,'organization_id':None})

    def test_null_organization_rejected_and_ozon_catalog_identity_preserved(self):
        for method,item in ((self.repo.upsert_product,{'id':'new'}),(self.repo.upsert_sku,{'id':'new','product_id':'p'}),(self.repo.upsert_listing,self.listing())):
            with self.assertRaises(ValueError):
                method({**item,'organization_id':None})
        self.repo.upsert_catalog_page('s',[{'product_id':123,'offer_id':'ozon-offer'}],sync_id='job')
        listing = self.repo.get_listing('s','ozon-offer')
        self.assertEqual('123',listing['external_id'])
        self.assertEqual('OZON:s:123',listing['product_id'])
        self.assertEqual('default',listing['organization_id'])


if __name__ == '__main__':
    unittest.main()
