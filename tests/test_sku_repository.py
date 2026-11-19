from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from pathlib import Path

from backend.repositories.erp import ErpRepository


class SkuRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        initialize_erp_test_database(Path(self.temp.name))
        self.repo = ErpRepository(Path(self.temp.name))
        self.repo.upsert_product({"id": "P000101", "source_platform": "1688", "status": "DRAFT"})

    def tearDown(self):
        self.temp.cleanup()

    def test_sku_upsert_is_idempotent_and_preserves_product_boundary(self):
        row = {
            "id": "P000101:SKU-A",
            "product_id": "P000101",
            "seller_sku": "SKU-A",
            "supplier_sku": "1688-A",
            "barcode": "460000000001",
            "color": "黑色",
            "size": "500ml",
            "specification": "黑色 500ml",
            "purchase_price_cny": 12.5,
            "weight": 320,
            "dimensions": {"length_mm": 120, "width_mm": 80, "height_mm": 80},
        }
        self.repo.upsert_sku(row)
        self.repo.upsert_sku({**row, "color": "蓝色"})

        product = self.repo.get_product("P000101")

        self.assertEqual(len(product["skus"]), 1)
        self.assertEqual(product["skus"][0]["color"], "蓝色")
        self.assertEqual(product["skus"][0]["dimensions"]["length_mm"], 120)


if __name__ == "__main__":
    unittest.main()
