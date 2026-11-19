from __future__ import annotations

import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class ListingReconciliationFailureTest(SyncCase, unittest.TestCase):
    def seed_old_listing(self) -> int:
        self.repo.upsert_product({"id": "LOCAL:old-offer", "status": "SYNCED"})
        return self.repo.upsert_listing({
            "store_id": "store-1",
            "product_id": "LOCAL:old-offer",
            "offer_id": "old-offer",
            "listing_status": "VISIBLE",
        })

    def test_partial_sync_does_not_reconcile_missing_listing(self):
        listing_id = self.seed_old_listing()
        client = CatalogClient({"": (products(1, 1), "next")}, fail_cursor="next")
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        row = self.repo.get_listing("store-1", "old-offer")
        self.assertEqual("PARTIAL", self.repo.get_sync_job(job_id)["status"])
        self.assertNotEqual("NOT_SEEN", row["presence_status"])
        self.assertEqual(listing_id, row["id"])

    def test_failed_sync_does_not_reconcile_or_delete_listing(self):
        listing_id = self.seed_old_listing()
        client = CatalogClient({"": ([], "")}, fail_cursor="")
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        row = self.repo.get_listing("store-1", "old-offer")
        self.assertEqual("FAILED", self.repo.get_sync_job(job_id)["status"])
        self.assertNotEqual("NOT_SEEN", row["presence_status"])
        self.assertEqual(listing_id, row["id"])


if __name__ == "__main__":
    unittest.main()
