import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.db.erp import SCHEMA, database, initialize


class UnifiedMarketplaceSchemaTest(unittest.TestCase):
    def test_migration_collision_rolls_back_without_losing_legacy_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "runtime/erp.sqlite3"
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.executescript(SCHEMA)
                db.execute("INSERT INTO stores(id,platform,display_name,currency,connection_status,credential_ref,created_at,updated_at) VALUES('s','OZON','Store','RUB','CONNECTED','','old','old')")
                db.execute("INSERT INTO products(id,source_platform,status,created_at,updated_at) VALUES('p','OZON','SYNCED','old','old')")
                for offer in ('a','b'):
                    db.execute("INSERT INTO marketplace_listings(store_id,product_id,platform,offer_id,ozon_product_id,listing_status,created_at,updated_at) VALUES('s','p','OZON',?,'123','ONLINE','old','old')", (offer,))
                db.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                initialize(root)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(2, db.execute('SELECT COUNT(*) FROM marketplace_listings').fetchone()[0])
                self.assertNotIn('external_id', {row[1] for row in db.execute('PRAGMA table_info(marketplace_listings)')})
                self.assertEqual(0, db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='009'").fetchone()[0])
                db.execute("UPDATE marketplace_listings SET ozon_product_id='456' WHERE offer_id='b'")
                db.commit()
            initialize(root)
            initialize(root)
            with database(root) as db:
                self.assertEqual([(1,'123'),(2,'456')], [tuple(row) for row in db.execute('SELECT id,external_id FROM marketplace_listings ORDER BY id')])

    def test_populated_pre_v11b_database_upgrade_preserves_rows_and_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "runtime/erp.sqlite3"
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.executescript(SCHEMA)
                db.execute("INSERT INTO stores(id,platform,display_name,currency,connection_status,credential_ref,created_at,updated_at) VALUES('s','OZON','Store','RUB','CONNECTED','existing-ref','old','old')")
                db.execute("INSERT INTO products(id,source_platform,status,created_at,updated_at) VALUES('p','1688','SYNCED','old','old')")
                db.execute("INSERT INTO skus(id,product_id,seller_sku,created_at,updated_at) VALUES('sku','p','offer','old','old')")
                db.execute("INSERT INTO marketplace_listings(id,store_id,product_id,sku_id,platform,offer_id,ozon_product_id,listing_status,created_at,updated_at) VALUES(42,'s','p','sku','OZON','offer','123','ONLINE','old','old')")
                db.execute("INSERT INTO sync_jobs(id,type,store_id,status,started_at) VALUES('job','OZON_PRODUCT_SYNC','s','SUCCEEDED','old')")
                db.execute("INSERT INTO audit_logs(action,entity_type,entity_id,actor,details_json,created_at) VALUES('SYNC','store','s','system','{}','old')")
                originals = {table: [dict(zip([col[1] for col in db.execute(f'PRAGMA table_info({table})')], row)) for row in db.execute(f'SELECT * FROM {table}')] for table in ('stores','products','skus','marketplace_listings','sync_jobs','audit_logs')}
                db.commit()
            initialize(root)
            initialize(root)
            with database(root) as db:
                for table, original in originals.items():
                    rows = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
                    self.assertEqual(original, [{key: row[key] for key in old} for row, old in zip(rows, original)])
                    self.assertEqual(len(original), len(rows))
                    self.assertTrue(all(row['organization_id'] == 'default' for row in rows))
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(f"UPDATE {table} SET organization_id=NULL")
                listing = db.execute('SELECT * FROM marketplace_listings').fetchone()
                self.assertEqual(('p', 'sku', 42, '123'), (listing['product_id'],listing['sku_id'],listing['id'],listing['external_id']))
                self.assertIsNone(db.execute('SELECT external_id FROM stores').fetchone()[0])
                self.assertEqual(('OZON','s'), tuple(db.execute('SELECT platform,store_id FROM audit_logs').fetchone()))
                self.assertEqual('OZON', db.execute('SELECT platform FROM sync_jobs').fetchone()[0])
                self.assertEqual([], db.execute('PRAGMA foreign_key_check').fetchall())
                self.assertEqual(1, db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='009'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
