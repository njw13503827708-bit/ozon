from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from pathlib import Path

from backend.repositories.erp import ErpRepository


class ProductRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        initialize_erp_test_database(Path(self.temp.name))
        self.repo = ErpRepository(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_upsert_and_filter_products_without_touching_artifacts(self):
        self.repo.upsert_product({
            "id": "P000101",
            "source_platform": "1688",
            "source_url": "https://detail.1688.com/offer/101.html",
            "source_product_id": "101",
            "title_cn": "不锈钢保温杯",
            "title_ru": "Термокружка",
            "description_ru": "Описание",
            "brand": "Нет бренда",
            "category_id": "170000",
            "type_id": "92000",
            "status": "READY_TO_PUBLISH",
        })

        page = self.repo.list_products(q="保温杯", status="READY_TO_PUBLISH")

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["id"], "P000101")
        self.assertEqual(page["items"][0]["title_ru"], "Термокружка")
        self.assertTrue((Path(self.temp.name) / "runtime/erp.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
