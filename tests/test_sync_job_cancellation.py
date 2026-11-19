from __future__ import annotations

import asyncio
import json
import unittest
from fastapi import FastAPI, Request
from unittest.mock import patch

from backend.api import ozon_products
from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService
from tests.ozon_sync_support import CatalogClient, SyncCase, products


def post_cancel(job_id: str, *, host: bool) -> tuple[int, dict]:
    app = FastAPI()

    @app.middleware("http")
    async def operator(request: Request, call_next):
        request.state.operator = {"is_host_device": host}
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

        path = f"/api/erp/sync-jobs/{job_id}/cancel"
        await app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "headers": [],
            "client": ("127.0.0.1", 12345), "server": ("test", 80), "root_path": "",
        }, receive, send)
        status = next(item["status"] for item in sent if item["type"] == "http.response.start")
        body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        return status, json.loads(body)

    return asyncio.run(request())


class CancelDuringDetailClient(CatalogClient):
    def __init__(self, pages, on_detail):
        super().__init__(pages)
        self.on_detail = on_detail
        self.attributes_called = False

    def get_product_info(self, product_ids):
        result = super().get_product_info(product_ids)
        self.on_detail()
        return result

    def get_product_attributes(self, offer_ids=(), product_ids=()):
        self.attributes_called = True
        return super().get_product_attributes(offer_ids, product_ids)


class CancelDuringListClient(CatalogClient):
    def __init__(self, pages, on_list):
        super().__init__(pages)
        self.on_list = on_list
        self.details_called = False

    def list_products(self, **kwargs):
        result = super().list_products(**kwargs)
        self.on_list()
        return result

    def get_product_info(self, product_ids):
        self.details_called = True
        return super().get_product_info(product_ids)


class CancelDuringAttributesClient(CatalogClient):
    def __init__(self, pages, on_attributes):
        super().__init__(pages)
        self.on_attributes = on_attributes

    def get_product_attributes(self, offer_ids=(), product_ids=()):
        result = super().get_product_attributes(offer_ids, product_ids)
        self.on_attributes()
        return result


class SyncJobCancellationTest(SyncCase, unittest.TestCase):
    def test_worker_cancels_before_first_request(self):
        client = CatalogClient({"": (products(1, 1), "")})
        job_id = self.create_job()
        self.repo.request_sync_job_cancel(job_id)

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        self.assertEqual([], client.list_calls)
        self.assertEqual("CANCELLED", self.repo.get_sync_job(job_id)["status"])

    def test_worker_cancels_after_persisted_list_page(self):
        job_id = self.create_job()
        client = CancelDuringListClient(
            {"": (products(1, 1), "next"), "next": (products(2, 1), "")},
            lambda: self.repo.request_sync_job_cancel(job_id),
        )

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        self.assertEqual(1, len(client.list_calls))
        self.assertFalse(client.details_called)
        self.assertEqual("CANCELLED", self.repo.get_sync_job(job_id)["status"])
        self.assertIsNotNone(self.repo.get_listing("store-1", "offer-1"))
        self.assertIsNone(self.repo.get_listing("store-1", "offer-2"))

    def test_cancel_api_only_requests_cancellation(self):
        job_id = self.create_job()
        self.repo.update_sync_job(job_id, status="RUNNING")
        with patch.object(ozon_products, "ROOT", self.root):
            forbidden_status, _ = post_cancel(job_id, host=False)
            status, body = post_cancel(job_id, host=True)

        job = self.repo.get_sync_job(job_id)
        self.assertEqual(403, forbidden_status)
        self.assertEqual(202, status)
        self.assertEqual(job_id, body["job_id"])
        self.assertEqual("RUNNING", job["status"])
        self.assertEqual(1, job["cancel_requested"])
        self.assertIsNotNone(job["cancel_requested_at"])

    def test_worker_cancels_after_persisted_detail_batch(self):
        self.repo.upsert_product({"id": "LOCAL:old", "status": "SYNCED"})
        self.repo.upsert_listing({
            "store_id": "store-1", "product_id": "LOCAL:old",
            "offer_id": "old-offer", "listing_status": "VISIBLE",
        })
        job_id = self.create_job()
        client = CancelDuringDetailClient(
            {"": (products(1, 1), "")},
            lambda: self.repo.request_sync_job_cancel(job_id),
        )

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        job = self.repo.get_sync_job(job_id)
        self.assertEqual("CANCELLED", job["status"])
        self.assertEqual("CANCELLED", job["stage"])
        self.assertIsNotNone(job["finished_at"])
        self.assertFalse(client.attributes_called)
        self.assertEqual("Product 1", self.repo.get_listing("store-1", "offer-1")["remote_title"])
        self.assertNotEqual("NOT_SEEN", self.repo.get_listing("store-1", "old-offer")["presence_status"])

    def test_worker_cancels_after_persisted_attribute_chunk(self):
        job_id = self.create_job()
        client = CancelDuringAttributesClient(
            {"": (products(1, 1), "")},
            lambda: self.repo.request_sync_job_cancel(job_id),
        )

        OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")

        listing = self.repo.get_listing("store-1", "offer-1")
        self.assertEqual("CANCELLED", self.repo.get_sync_job(job_id)["status"])
        self.assertEqual("SYNCED", listing["attributes_status"])
        self.assertEqual({"offer_id": "offer-1"}, listing["attributes"])


if __name__ == "__main__":
    unittest.main()
