import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class NoAnalyticsClient(CatalogClient):
    def get_product_query_details(self, *args, **kwargs):
        raise AssertionError("catalog sync must not call search analytics")


class OzonProductSyncWithoutAnalyticsTest(SyncCase, unittest.TestCase):
    def test_catalog_sync_does_not_require_search_analytics(self):
        client = NoAnalyticsClient({"": (products(1, 2), "")})
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        self.assertEqual("SUCCEEDED", self.repo.get_sync_job(job_id)["status"])
        self.assertEqual(2, self.repo.list_products(store_id="store-1")["total"])


if __name__ == "__main__":
    unittest.main()
