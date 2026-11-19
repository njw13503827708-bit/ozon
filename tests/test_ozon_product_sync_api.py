from backend.db.erp import initialize as initialize_erp_test_database
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI, Request

from backend.api import ozon_products


class AsgiResponse:
    def __init__(self, messages):
        start = next(item for item in messages if item["type"] == "http.response.start")
        self.status_code = start["status"]
        self.body = b"".join(
            item.get("body", b"")
            for item in messages
            if item["type"] == "http.response.body"
        )
        self.text = self.body.decode("utf-8")

    def json(self):
        return json.loads(self.body)


class OzonProductSyncApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        initialize_erp_test_database(self.root)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def post(operator):
        app = FastAPI()

        @app.middleware("http")
        async def set_operator(request: Request, call_next):
            request.state.operator = operator
            return await call_next(request)

        app.include_router(ozon_products.router)

        async def request():
            sent = []
            received = False

            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message):
                sent.append(message)

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "path": "/api/erp/stores/store-1/products/sync",
                    "raw_path": b"/api/erp/stores/store-1/products/sync",
                    "query_string": b"",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                    "server": ("test", 80),
                    "root_path": "",
                },
                receive,
                send,
            )
            return AsgiResponse(sent)

        return asyncio.run(request())

    def test_non_host_cannot_start_product_sync(self):
        credentials = Mock(side_effect=AssertionError("credentials must not be read"))
        with patch.object(ozon_products, "ROOT", self.root), patch.object(
            ozon_products, "_store_credentials", credentials
        ):
            response = self.post({"id": "remote", "is_host_device": False})

        self.assertEqual(403, response.status_code)
        credentials.assert_not_called()

    def test_host_can_start_product_sync_without_credentials_in_response(self):
        shop = {"id": "store-1", "display_name": "Store 1"}
        queued = {"job_id": "job-1", "status": "QUEUED", "reused": False}
        with patch.object(ozon_products, "ROOT", self.root), patch.object(
            ozon_products,
            "_store_credentials",
            return_value=(shop, {"client_id": "client-secret", "api_key": "api-secret"}),
        ), patch.object(ozon_products, "OzonProductClient"), patch.object(
            ozon_products, "OzonCatalogSyncService"
        ), patch.object(ozon_products, "queue_catalog_sync", return_value=queued):
            response = self.post({"id": "host", "is_host_device": True})

        self.assertEqual(202, response.status_code)
        self.assertEqual({"job_id", "status", "reused"}, set(response.json()))
        self.assertNotIn("secret", response.text.lower())

    def test_unreadable_store_credentials_return_409_not_server_error(self):
        from fastapi import HTTPException

        from scripts.workbench_stores import upsert_store

        upsert_store(
            self.root,
            {"id": "store-1", "display_name": "Store 1", "client_id": "client-secret", "api_key": "api-secret"},
        )
        with patch.object(ozon_products, "ROOT", self.root), patch(
            "backend.integrations.ozon.credential_store.CredentialStore._secure",
            side_effect=OSError("ACL denied"),
        ):
            with self.assertRaises(HTTPException) as caught:
                ozon_products._store_credentials("store-1")
        self.assertEqual(409, caught.exception.status_code)


if __name__ == "__main__":
    unittest.main()
