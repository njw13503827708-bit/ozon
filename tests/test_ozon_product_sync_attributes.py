import unittest

from backend.integrations.ozon.product_client import OzonProductClient
from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class OzonProductSyncAttributesTest(SyncCase, unittest.TestCase):
    def test_client_accepts_attribute_result_array(self):
        client = OzonProductClient(
            "client", "secret",
            transport=lambda endpoint, payload: {"result": [{"offer_id": "offer-1"}]},
        )

        self.assertEqual("offer-1", client.get_product_attributes(offer_ids=["offer-1"])["result"][0]["offer_id"])

    def test_attributes_failure_does_not_hide_synced_products(self):
        client = CatalogClient(
            {"": (products(1, 2), "")},
            attributes_error=RuntimeError("attributes unavailable"),
        )
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        page = self.repo.list_products(store_id="store-1")
        self.assertEqual(2, page["total"])
        self.assertEqual("PARTIAL", self.repo.get_sync_job(job_id)["status"])
        self.assertEqual({"FAILED"}, {item["attributes_status"] for item in page["items"]})


if __name__ == "__main__":
    unittest.main()
