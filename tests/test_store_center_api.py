from backend.db.erp import initialize as initialize_erp_test_database
import asyncio
import json
import tempfile
import unittest
from urllib.parse import urlencode
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from backend.api import ozon_products, stores
from backend.repositories.erp import ErpRepository


class StoreCenterApiTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        initialize_erp_test_database(self.root)
        self.repo = ErpRepository(self.root)
        self.repo.upsert_store({"id": "one", "display_name": "Test store", "credential_ref": "ozon-adapter/.env.one",
                                "connection_status": "CONNECTED", "seller_id": "client-secret", "last_error": "raw-api-secret"})
        self.root_patch = patch.object(stores, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def request(self, method="GET", path="/api/erp/stores", host=True, query=None):
        app = FastAPI()
        app.include_router(stores.router)
        app.include_router(ozon_products.router)

        async def run():
            messages = []

            async def receive():
                return {"type": "http.request", "body": b""}

            async def send(message):
                messages.append(message)

            await app({"type": "http", "method": method, "path": path, "query_string": urlencode(query or {}).encode(),
                       "headers": [], "state": {"operator": {"is_host_device": host}},
                       "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 1)}, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            body = b"".join(m.get("body", b"") for m in messages)
            for secret in (b".env.one", b"credential_ref", b"api-secret", b"client-secret"):
                self.assertNotIn(secret, body)
            return status, json.loads(body)

        return asyncio.run(run())

    def test_no_match_is_not_an_empty_database(self):
        status, body = self.request(query={"q": "no-match"})
        self.assertEqual(200, status)
        self.assertEqual(0, body["total"])
        self.assertEqual(1, body["unfiltered_total"])
        with patch.object(self.repo, "list_stores", return_value=[]), patch.object(stores, "_reader", return_value=stores.StoreService(self.repo, None, None)):
            self.assertEqual(0, self.request()[1]["unfiltered_total"])

    def validate(self, products=None, account=None):
        account = account or {"company": {"name": "Official company", "currency": "RUB"}}
        with patch.object(stores, "_store_credentials", return_value=({}, {"client_id": "client-secret", "api_key": "api-secret"})), \
             patch.object(stores, "OzonAccountClient") as account_class, patch.object(stores, "OzonProductClient") as product_class:
            account_class.return_value.get_seller_info.return_value = account
            if isinstance(products, Exception):
                product_class.return_value.list_products.side_effect = products
            else:
                product_class.return_value.list_products.return_value = products or {"result": {"items": []}}
            result = self.request("POST", "/api/erp/stores/one/validate")
            product_class.return_value.list_products.assert_called_once_with(limit=1)
            return result

    def test_read_legacy_connected_is_unknown_and_no_credentials(self):
        status, result = self.request()
        self.assertEqual(200, status)
        store = result["items"][0]
        self.assertIsNone(store["seller_id"])
        self.assertIsNone(store["product_read_allowed"])
        self.assertIsNone(store["company_name"])
        self.assertIsNone(store["last_connected_at"])
        self.assertIsNone(store["last_product_sync_at"])
        self.assertEqual(0, store["product_count"])

    def test_store_name_status_and_pagination(self):
        self.repo.upsert_store({"id": "two", "display_name": "Second shop", "platform": "OZON", "connection_status": "FAILED"})
        status, result = self.request(query={"q": "Second", "connection_status": "FAILED", "page_size": 1})
        self.assertEqual(200, status)
        self.assertEqual(1, result["total"])
        self.assertEqual(["two"], [item["id"] for item in result["items"]])
        self.assertEqual(1, result["page_size"])
        self.assertIn("currency", result["items"][0])
        self.assertIn("listing_count", result["items"][0])

    def test_real_validation_persists_probe_and_sync_count(self):
        self.repo.upsert_catalog_page("one", [{"product_id": 123, "offer_id": "offer"}], sync_id="sync")
        self.repo.update_store_last_sync("one")
        status, result = self.validate()
        self.assertEqual(200, status)
        self.assertEqual("Official company", result["company_name"])
        self.assertIsNone(result["seller_id"])
        self.assertEqual("CONNECTED", result["connection_status"])
        self.assertTrue(result["product_read_allowed"])
        self.assertTrue(result["last_connected_at"])
        self.assertTrue(result["last_product_sync_at"])
        self.assertEqual(1, result["product_count"])
        self.assertTrue(self.request()[1]["items"][0]["product_read_allowed"])

    def test_failed_probe_invalidates_previous_success_without_claiming_denial(self):
        self.validate()
        status, _ = self.validate(TimeoutError("raw api-secret"))
        self.assertEqual(502, status)
        item = self.request()[1]["items"][0]
        self.assertIsNone(item["product_read_allowed"])
        self.assertEqual("FAILED", item["connection_status"])

    def test_product_sync_preserves_verified_store_state(self):
        self.validate()
        before = self.repo.get_store("one")
        with patch.object(ozon_products, "ROOT", self.root), \
             patch.object(ozon_products, "_store_credentials", return_value=({"display_name": "Registry name"}, {"client_id": "client-secret", "api_key": "api-secret"})), \
             patch.object(ozon_products, "OzonProductClient"), \
             patch.object(ozon_products, "queue_catalog_sync", return_value={"job_id": "job", "status": "QUEUED"}) as queue:
            self.assertEqual(202, self.request("POST", "/api/erp/stores/one/products/sync")[0])
            queue.assert_called_once()
        after = self.repo.get_store("one")
        for key in ("company_name", "seller_id", "connection_status", "last_connected_at", "last_error"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual("Registry name", after["display_name"])
        self.assertTrue(self.request()[1]["items"][0]["product_read_allowed"])

    def test_host_gate_runs_before_credentials(self):
        with patch.object(stores, "_store_credentials") as credentials:
            self.assertEqual(403, self.request("POST", "/api/erp/stores/one/validate", False)[0])
            credentials.assert_not_called()

    def test_credential_failure_clears_previous_probe_without_raw_error(self):
        self.validate()
        with patch.object(stores, "_store_credentials", side_effect=OSError("api-secret")):
            self.assertEqual(409, self.request("POST", "/api/erp/stores/one/validate")[0])
        self.assertIsNone(self.request()[1]["items"][0]["product_read_allowed"])

    def test_credential_reference_mismatch_invalidates_previous_validation(self):
        self.validate()
        self.repo.upsert_store({**self.repo.get_store("one"), "credential_ref": "wrong-api-secret"})
        with patch.object(stores, "_store_credentials") as credentials:
            self.assertEqual(409, self.request("POST", "/api/erp/stores/one/validate")[0])
            credentials.assert_not_called()
        item = self.request()[1]["items"][0]
        self.assertEqual("FAILED", item["connection_status"])
        self.assertIsNone(item["product_read_allowed"])
        self.assertNotIn("api-secret", self.repo.get_store("one")["last_error"])

    def test_whitespace_credentials_invalidate_previous_validation(self):
        self.validate()
        with patch.object(stores, "_store_credentials", return_value=({}, {"client_id": " ", "api_key": " "})):
            status, _ = self.request("POST", "/api/erp/stores/one/validate")
        item = self.request()[1]["items"][0]
        self.assertEqual("FAILED", item["connection_status"])
        self.assertIsNone(item["product_read_allowed"])
        self.assertEqual(409, status)
        self.assertEqual("Credential read failed", self.repo.get_store("one")["last_error"])

    def test_unknown_disabled_and_non_ozon_stores_do_not_probe(self):
        with patch.object(stores, "_store_credentials") as credentials:
            self.assertEqual(404, self.request("POST", "/api/erp/stores/missing/validate")[0])
            for index, values in enumerate(({"enabled": False}, {"platform": "KASPI"})):
                self.repo.upsert_store({"id": f"unsupported-{index}", **values})
                self.assertEqual(409, self.request("POST", f"/api/erp/stores/unsupported-{index}/validate")[0])
            credentials.assert_not_called()

    def test_empty_and_missing_detail(self):
        with patch.object(stores, "ROOT", self.root / "empty"):
            initialize_erp_test_database(self.root / "empty")
            self.assertEqual({"items": [], "total": 0, "unfiltered_total": 0, "page": 1, "page_size": 200}, self.request()[1])
        self.assertEqual(404, self.request(path="/api/erp/stores/missing")[0])


if __name__ == "__main__":
    unittest.main()
