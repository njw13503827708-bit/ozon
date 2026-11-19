import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class OzonProductListPaginationTest(SyncCase, unittest.TestCase):
    def test_2500_products_are_saved_page_by_page(self):
        client = CatalogClient({
            "": (products(1, 1000), "page-2"),
            "page-2": (products(1001, 1000), "page-3"),
            "page-3": (products(2001, 500), ""),
        })
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        self.assertEqual([(1000, ""), (1000, "page-2"), (1000, "page-3")], client.list_calls)
        self.assertEqual(2500, self.repo.list_products(store_id="store-1", page_size=200)["total"])
        self.assertEqual("SUCCEEDED", self.repo.get_sync_job(job_id)["status"])


if __name__ == "__main__":
    unittest.main()
