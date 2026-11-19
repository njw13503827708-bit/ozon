from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from pathlib import Path

from backend.repositories.erp import ErpRepository


class MarketplaceListingRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        initialize_erp_test_database(Path(self.temp.name))
        self.repo = ErpRepository(Path(self.temp.name))
        self.repo.upsert_store({
            "id": "shop-a", "platform": "OZON", "display_name": "店铺A",
            "seller_id": "123", "company_name": "ООО Ромашка", "currency": "RUB",
            "enabled": True, "connection_status": "CONNECTED", "credential_ref": "ozon-adapter/.env.shop-a",
        })
        self.repo.upsert_product({"id": "ERP-1", "source_platform": "OZON", "status": "ONLINE"})
        self.repo.upsert_sku({"id": "ERP-1:OFFER-1", "product_id": "ERP-1", "seller_sku": "OFFER-1"})

    def tearDown(self):
        self.temp.cleanup()

    def test_listing_is_distinct_from_product_and_unique_per_store_offer(self):
        listing = {
            "store_id": "shop-a", "product_id": "ERP-1", "sku_id": "ERP-1:OFFER-1",
            "platform": "OZON", "offer_id": "OFFER-1", "ozon_product_id": "991",
            "ozon_sku": "88001", "listing_status": "ONLINE", "moderation_status": "APPROVED",
            "remote_title": "Термокружка", "remote_price": 1299, "remote_stock": 7,
        }
        first = self.repo.upsert_listing(listing)
        second = self.repo.upsert_listing({**listing, "remote_price": 1399})

        product = self.repo.get_product("ERP-1")

        self.assertEqual(first, second)
        self.assertEqual(len(product["listings"]), 1)
        self.assertEqual(product["listings"][0]["remote_price"], 1399)
        self.assertEqual(product["listings"][0]["store_display_name"], "店铺A")


if __name__ == "__main__":
    unittest.main()
