from __future__ import annotations

import unittest

from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


class AttributeClient(CatalogClient):
    def __init__(self, attribute_items):
        super().__init__({"": (products(1, 2), "")})
        self.attribute_items = attribute_items

    def get_product_attributes(self, offer_ids=(), product_ids=()):
        return {"result": self.attribute_items}


class OzonAttributePersistenceTest(SyncCase, unittest.TestCase):
    def test_attribute_response_is_persisted_on_the_matching_listing(self):
        response_item = {
            "offer_id": "offer-1",
            "id": 1,
            "attributes": [{"id": 85, "values": [{"value": "Красный"}]}],
        }
        client = AttributeClient([
            response_item,
            {"offer_id": "offer-2", "id": 2, "attributes": []},
        ])
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        listing = self.repo.get_listing("store-1", "offer-1")
        self.assertEqual(response_item, listing["attributes"])
        self.assertEqual("SYNCED", listing["attributes_status"])
        self.assertIsNotNone(listing["attributes_synced_at"])

    def test_malformed_attribute_item_fails_only_the_unmatched_offer(self):
        successful = {
            "offer_id": "offer-1",
            "id": 1,
            "attributes": [{"id": 85, "values": [{"value": "Красный"}]}],
        }
        client = AttributeClient([successful, {"id": 2, "attributes": []}])
        job_id = self.create_job()

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        first = self.repo.get_listing("store-1", "offer-1")
        second = self.repo.get_listing("store-1", "offer-2")
        self.assertEqual(successful, first["attributes"])
        self.assertEqual("SYNCED", first["attributes_status"])
        self.assertEqual("FAILED", second["attributes_status"])
        self.assertIn("omitted requested offer", second["last_error"])
        self.assertEqual("PARTIAL", self.repo.get_sync_job(job_id)["status"])


if __name__ == "__main__":
    unittest.main()
