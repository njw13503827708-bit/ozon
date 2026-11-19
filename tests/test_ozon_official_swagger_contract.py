from backend.db.erp import initialize as initialize_erp_test_database
"""Behavioral contracts derived only from the user-supplied Seller API Swagger."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.integrations.ozon.account_client import OzonAccountClient
from backend.integrations.ozon.product_client import OzonProductClient
from backend.repositories.erp import ErpRepository
from backend.services.store_service import StoreService


SWAGGER = json.loads((Path(__file__).resolve().parents[1] / "docs/ozon/swagger.json").read_text(encoding="utf-8"))


class OfficialSwaggerContractTest(unittest.TestCase):
    def test_seller_info_uses_documented_post_headers_without_request_body(self):
        operation = SWAGGER["paths"]["/v1/seller/info"]["post"]
        self.assertEqual("SellerAPI_SellerInfo", operation["operationId"])
        self.assertNotIn("requestBody", operation)
        self.assertFalse(operation.get("deprecated", False))
        self.assertEqual(
            {"Client-Id", "Api-Key"},
            {SWAGGER["components"]["parameters"][p["$ref"].rsplit("/", 1)[-1]]["name"] for p in operation["parameters"]},
        )
        response = io.BytesIO(b'{"company":{"name":"Test Seller","currency":"RUB"}}')
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request)
            return response

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            OzonAccountClient("client-123", "secret-key").get_seller_info()
        self.assertEqual("/v1/seller/info", captured[0].selector)
        self.assertEqual("POST", captured[0].get_method())
        self.assertIsNone(captured[0].data)
        self.assertEqual("client-123", captured[0].get_header("Client-id"))
        self.assertEqual("secret-key", captured[0].get_header("Api-key"))

    def test_store_identity_does_not_invent_seller_id_absent_from_response(self):
        seller_schema = SWAGGER["components"]["schemas"]["v1SellerInfoResponse"]
        company_schema = SWAGGER["components"]["schemas"]["SellerInfoResponseCompany"]
        self.assertNotIn("seller_id", seller_schema["properties"])
        self.assertNotIn("seller_id", company_schema["properties"])
        self.assertIn("name", company_schema["properties"])
        with tempfile.TemporaryDirectory() as directory:
            initialize_erp_test_database(Path(directory))
            repository = ErpRepository(Path(directory))
            repository.upsert_store({"id": "store-1"})
            account = OzonAccountClient(
                "client-123", "secret-key",
                transport=lambda *_: {"company": {"name": "Test Seller", "currency": "RUB"}},
            )
            products = OzonProductClient(
                "client-123", "secret-key",
                transport=lambda *_: {"result": {"items": [], "last_id": ""}},
            )
            result = StoreService(repository, account, products).validate_credentials("store-1")
            self.assertIsNone(result["seller_id"])
            self.assertIsNone(repository.get_store("store-1")["seller_id"])
            self.assertEqual("Test Seller", result["company_name"])

    def test_product_read_probe_omits_empty_identifier_filters(self):
        operation = SWAGGER["paths"]["/v3/product/list"]["post"]
        self.assertEqual("ProductAPI_GetProductList", operation["operationId"])
        self.assertFalse(operation.get("deprecated", False))
        filter_schema = SWAGGER["components"]["schemas"]["productv3GetProductListRequestFilter"]
        self.assertEqual(1, filter_schema["properties"]["offer_id"]["minItems"])
        self.assertEqual(1, filter_schema["properties"]["product_id"]["minItems"])
        captured = []

        def transport(endpoint, payload):
            captured.append((endpoint, payload))
            return {"result": {"items": [], "last_id": ""}}

        OzonProductClient("client-123", "secret-key", transport=transport).list_products(limit=1)
        self.assertEqual("/v3/product/list", captured[0][0])
        self.assertEqual({"filter": {"visibility": "ALL"}, "last_id": "", "limit": 1}, captured[0][1])


if __name__ == "__main__":
    unittest.main()
