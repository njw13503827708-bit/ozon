from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from pathlib import Path

from backend.repositories.erp import ErpRepository


class PlatformSkusTest(unittest.TestCase):
    def test_all_source_identifiers_are_searchable_without_creating_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize_erp_test_database(Path(directory))
            repo = ErpRepository(Path(directory))
            for store in ("a", "b"):
                repo.upsert_store({"id": store, "platform": "OZON"})
                repo.upsert_catalog_page(store, [{"product_id": 123, "offer_id": "MUG"}], sync_id="test")
                repo.update_listing_details(store, [{
                    "id": 123, "offer_id": "MUG", "sku": 100,
                    "sources": [
                        {"sku": 101, "source": "fbo", "shipment_type": "SHIPMENT_TYPE_GENERAL"},
                        {"sku": 102, "source": "fbs", "shipment_type": "SHIPMENT_TYPE_GENERAL"},
                        {"sku": 101, "source": "fbo", "shipment_type": "SHIPMENT_TYPE_GENERAL"},
                    ],
                }])
            page = repo.list_product_center(q="102", store_id="a")
            self.assertEqual(1, page["total"])
            item = page["items"][0]
            self.assertEqual("100", item["ozon_sku"])
            self.assertEqual({"100", "101", "102"}, {row["sku"] for row in item["platform_skus"]})
            detail = repo.get_product_center_detail(item["product_id"])
            self.assertEqual(1, len(detail["skus"]))
            self.assertEqual(3, len(detail["listings"][0]["platform_skus"]))
            repo.update_listing_details("a", [{"id": 123, "offer_id": "MUG", "sku": 100,
                                              "sources": [{"sku": 103, "source": "sds"}]}])
            self.assertEqual(0, repo.list_product_center(q="102", store_id="a")["total"])
            self.assertEqual(1, repo.list_product_center(q="102", store_id="b")["total"])

    def test_sources_only_update_retains_previously_observed_top_sku(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize_erp_test_database(Path(directory))
            repo = ErpRepository(Path(directory))
            repo.upsert_store({"id": "a", "platform": "OZON"})
            repo.upsert_catalog_page("a", [{"product_id": 123, "offer_id": "MUG"}], sync_id="test")
            repo.update_listing_details("a", [{"id": 123, "sku": 100, "sources": [{"sku": 101, "source": "fbo"}]}])
            repo.update_listing_details("a", [{"id": 123, "sources": [{"sku": 102, "source": "fbs"}]}])
            listing = repo.get_product_center_detail("OZON:a:123")["listings"][0]
            self.assertEqual({"100", "102"}, {row["sku"] for row in listing["platform_skus"]})

    def test_sources_only_preserves_existing_local_sku_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize_erp_test_database(root)
            repo = ErpRepository(root)
            repo.upsert_store({"id": "a", "platform": "OZON"})
            repo.upsert_catalog_page("a", [{"product_id": 123, "offer_id": "MUG"}], sync_id="test")
            before = repo.get_product_center_detail("OZON:a:123")
            repo.update_listing_details("a", [{"id": 123, "offer_id": "MUG",
                                              "sources": [{"sku": 901, "source": "fbs"}]}])
            reopened = ErpRepository(root)
            result = reopened.list_product_center(q="901", store_id="a")
            self.assertEqual(1, result["total"])
            self.assertEqual(before["skus"], reopened.get_product_center_detail("OZON:a:123")["skus"])
            self.assertEqual("901", result["items"][0]["platform_skus"][0]["sku"])


if __name__ == "__main__":
    unittest.main()
