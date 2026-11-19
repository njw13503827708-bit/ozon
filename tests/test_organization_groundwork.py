from backend.db.erp import initialize as initialize_erp_test_database
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.db.erp import initialize
from backend.repositories.erp import ErpRepository


class OrganizationGroundworkTest(unittest.TestCase):
    def test_repository_rejects_explicit_null_organization_on_insert(self):
        with tempfile.TemporaryDirectory() as temporary:
            initialize_erp_test_database(Path(temporary))
            repo = ErpRepository(Path(temporary))
            with self.assertRaises(ValueError):
                repo.upsert_store({"id": "new-store", "organization_id": None})
            self.assertIsNone(repo.get_store("new-store"))

    def test_repository_rejects_explicit_null_organization_on_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            initialize_erp_test_database(Path(temporary))
            repo = ErpRepository(Path(temporary))
            repo.upsert_store({"id": "existing-store", "display_name": "Original"})
            original = repo.get_store("existing-store")
            with self.assertRaises(ValueError):
                repo.upsert_store({"id": "existing-store", "display_name": "Changed",
                                   "organization_id": None})
            self.assertEqual(original, repo.get_store("existing-store"))

    def test_migrates_existing_stores_without_losing_data_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "runtime/erp.sqlite3"
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.execute("""CREATE TABLE stores (
                    id TEXT PRIMARY KEY, platform TEXT NOT NULL, display_name TEXT NOT NULL,
                    seller_id TEXT, company_name TEXT, currency TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, connection_status TEXT NOT NULL,
                    credential_ref TEXT NOT NULL, last_connected_at TEXT, last_sync_at TEXT,
                    last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )""")
                db.executemany("""INSERT INTO stores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                    ("shop-a", "OZON", "店铺 A", "seller-a", "Company A", "RUB", 1,
                     "CONNECTED", "ozon-adapter/.env.shop-a", "2026-09-01", "2026-09-02",
                     None, "2026-08-01", "2026-09-02"),
                    ("shop-b", "OZON", "店铺 B", None, "Company B", "CNY", 0,
                     "FAILED", "ozon-adapter/.env.shop-b", None, None,
                     "validation failed", "2026-08-03", "2026-09-03"),
                ])
                db.commit()

            initialize(root)
            initialize(root)

            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA foreign_keys=ON")
                self.assertIn("organizations", {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")})
                organizations = db.execute("SELECT id, name FROM organizations").fetchall()
                stores = db.execute("""SELECT id, platform, display_name, seller_id, company_name,
                    currency, enabled, connection_status, credential_ref, last_connected_at,
                    last_sync_at, last_error, created_at, updated_at, organization_id
                    FROM stores ORDER BY id""").fetchall()
                column = next(row for row in db.execute("PRAGMA table_info(stores)")
                              if row[1] == "organization_id")
                versions = db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='008'").fetchone()[0]
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE stores SET organization_id=NULL WHERE id='shop-a'")

            self.assertEqual([("default", "Default Organization")], organizations)
            self.assertEqual([
                ("shop-a", "OZON", "店铺 A", "seller-a", "Company A", "RUB", 1, "CONNECTED",
                 "ozon-adapter/.env.shop-a", "2026-09-01", "2026-09-02", None,
                 "2026-08-01", "2026-09-02", "default"),
                ("shop-b", "OZON", "店铺 B", None, "Company B", "CNY", 0, "FAILED",
                 "ozon-adapter/.env.shop-b", None, None, "validation failed",
                 "2026-08-03", "2026-09-03", "default"),
            ], stores)
            self.assertEqual(1, column[3])  # SQLite NOT NULL flag
            self.assertEqual(1, versions)

    def test_repository_new_store_uses_default_organization(self):
        with tempfile.TemporaryDirectory() as temporary:
            initialize_erp_test_database(Path(temporary))
            repo = ErpRepository(Path(temporary))
            repo.upsert_store({"id": "new-store", "display_name": "New Store"})
            self.assertIn("organization_id", repo.get_store("new-store"))
            self.assertEqual("default", repo.get_store("new-store")["organization_id"])
            self.assertEqual("default", repo.list_stores()[0]["organization_id"])
            repo.upsert_store({"id": "new-store", "display_name": "Updated Store",
                               "organization_id": "untrusted-organization"})
            self.assertEqual("default", repo.get_store("new-store")["organization_id"])
            self.assertEqual("Updated Store", repo.get_store("new-store")["display_name"])


if __name__ == "__main__":
    unittest.main()
