from __future__ import annotations

import unittest
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from backend.db.erp import initialize
from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class ListingReconciliationTest(SyncCase, unittest.TestCase):
    def seed_listing(self, offer_id: str) -> None:
        product_id = f"LOCAL:{offer_id}"
        self.repo.upsert_product({"id": product_id, "status": "SYNCED"})
        self.repo.upsert_listing({
            "store_id": "store-1",
            "product_id": product_id,
            "offer_id": offer_id,
            "listing_status": "VISIBLE",
        })

    def test_complete_sync_marks_missing_old_listing_not_seen(self):
        self.seed_listing("old-offer")
        client = CatalogClient({"": (products(1, 1), "")})
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        rows = {row["offer_id"]: row for row in self.repo.list_store_listings("store-1")}
        self.assertEqual("NOT_SEEN", rows["old-offer"]["presence_status"])
        self.assertEqual("SEEN", rows["offer-1"]["presence_status"])
        self.assertEqual(job_id, rows["offer-1"]["last_seen_sync_id"])
        self.assertIsNotNone(rows["old-offer"]["id"])

    def test_existing_listing_table_is_migrated_before_presence_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "runtime/erp.sqlite3"
            path.parent.mkdir()
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """CREATE TABLE marketplace_listings (
                        id INTEGER PRIMARY KEY, store_id TEXT, product_id TEXT,
                        platform TEXT, offer_id TEXT, listing_status TEXT
                    )"""
                )

            initialize(root)

            with closing(sqlite3.connect(path)) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(marketplace_listings)")}
                indexes = {row[1] for row in db.execute("PRAGMA index_list(marketplace_listings)")}
            self.assertIn("presence_status", columns)
            self.assertIn("last_seen_sync_id", columns)
            self.assertIn("attributes_json", columns)
            self.assertIn("idx_listings_store_presence", indexes)


if __name__ == "__main__":
    unittest.main()
