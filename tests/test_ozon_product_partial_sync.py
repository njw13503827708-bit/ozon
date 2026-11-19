import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class OzonProductPartialSyncTest(SyncCase, unittest.TestCase):
    def test_saved_pages_survive_a_later_page_failure(self):
        client = CatalogClient({
            "": (products(1, 1000), "page-2"),
            "page-2": (products(1001, 1000), "page-3"),
        }, fail_cursor="page-3")
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        self.assertEqual(2000, self.repo.list_products(store_id="store-1", page_size=200)["total"])
        job = self.repo.get_sync_job(job_id)
        self.assertEqual("PARTIAL", job["status"])
        self.assertEqual("PRODUCT_LIST", job["stage"])


if __name__ == "__main__":
    unittest.main()
