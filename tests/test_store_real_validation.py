from backend.db.erp import initialize as initialize_erp_test_database
import tempfile
import unittest
from pathlib import Path

from backend.repositories.erp import ErpRepository
from backend.services.store_service import ProductReadPermissionError, StoreService


class FakeAccountClient:
    client_id = "client-123"

    def get_seller_info(self):
        return {"company": {"name": "ООО Тест", "currency": "RUB"}}


class FakeProductClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def list_products(self, *, limit):
        self.calls.append(limit)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class StoreRealValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        initialize_erp_test_database(Path(self.temp.name))
        self.repo = ErpRepository(Path(self.temp.name))
        self.repo.upsert_store({"id": "store-1", "display_name": "Store 1", "credential_ref": "ozon-adapter/.env.store-1"})

    def test_requires_seller_identity_and_product_read(self):
        products = FakeProductClient({"result": {"items": [], "last_id": ""}})
        result = StoreService(self.repo, FakeAccountClient(), products).validate_credentials("store-1")
        self.assertIsNone(result["seller_id"])
        self.assertEqual("ООО Тест", result["company_name"])
        self.assertTrue(result["product_read_allowed"])
        self.assertEqual([1], products.calls)
        self.assertEqual("CONNECTED", self.repo.get_store("store-1")["connection_status"])

    def test_category_tree_response_cannot_mark_connected(self):
        products = FakeProductClient({"result": []})
        with self.assertRaises(ProductReadPermissionError):
            StoreService(self.repo, FakeAccountClient(), products).validate_credentials("store-1")
        self.assertNotEqual("CONNECTED", self.repo.get_store("store-1")["connection_status"])

    def test_product_read_failure_cannot_mark_connected(self):
        products = FakeProductClient(PermissionError("secret-key denied"))
        with self.assertRaises(ProductReadPermissionError) as caught:
            StoreService(self.repo, FakeAccountClient(), products).validate_credentials("store-1")
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertNotEqual("CONNECTED", self.repo.get_store("store-1")["connection_status"])


if __name__ == "__main__":
    unittest.main()
