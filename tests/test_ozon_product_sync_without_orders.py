import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class NoOrdersClient(CatalogClient):
    def list_fbo_postings(self, *args, **kwargs):
        raise AssertionError("catalog sync must not call FBO orders")

    def list_fbs_postings(self, *args, **kwargs):
        raise AssertionError("catalog sync must not call FBS orders")


class OzonProductSyncWithoutOrdersTest(SyncCase, unittest.TestCase):
    def test_catalog_sync_does_not_require_orders(self):
        client = NoOrdersClient({"": (products(1, 2), "")})
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        self.assertEqual("SUCCEEDED", self.repo.get_sync_job(job_id)["status"])
        self.assertEqual(2, self.repo.list_products(store_id="store-1")["total"])


if __name__ == "__main__":
    unittest.main()
