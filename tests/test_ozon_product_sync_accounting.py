import json
import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class OzonProductSyncAccountingTest(SyncCase, unittest.TestCase):
    def test_malformed_row_is_recorded_and_job_is_partial(self):
        client = CatalogClient(
            {"": ([products(1, 1)[0], {"offer_id": "missing-id"}], "")}
        )
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        job = self.repo.get_sync_job(job_id)
        self.assertEqual("PARTIAL", job["status"])
        self.assertEqual(2, job["total"])
        self.assertEqual(1, job["success_count"])
        self.assertEqual(1, job["failure_count"])
        items = self.repo.list_sync_job_items(job_id)
        self.assertEqual(["FAILED", "SUCCEEDED"], sorted(item["status"] for item in items))

    def test_missing_detail_and_failed_attributes_are_partial_and_sanitized(self):
        client = CatalogClient(
            {"": (products(1, 2), "")},
            omitted_detail_ids={2},
            failed_attribute_offers={"offer-1"},
        )
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        job = self.repo.get_sync_job(job_id)
        self.assertEqual("PARTIAL", job["status"])
        self.assertEqual(0, job["success_count"])
        self.assertEqual(2, job["failure_count"])
        self.assertIsNotNone(self.repo.get_store("store-1")["last_sync_at"])
        evidence = json.dumps(
            {
                "items": self.repo.list_sync_job_items(job_id),
                "audits": self.repo.list_audit_logs("store-1"),
            },
            ensure_ascii=False,
        )
        self.assertNotIn("client-secret", evidence)
        self.assertNotIn("api-secret", evidence)


if __name__ == "__main__":
    unittest.main()
