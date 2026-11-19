from backend.db.erp import initialize as initialize_erp_test_database
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import FastAPI

from backend.api import ozon_products
from backend.db.erp import database
from backend.repositories.erp import ErpRepository


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


class ProductCenterApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        initialize_erp_test_database(self.root)
        self.repo = ErpRepository(self.root)
        self.repo.upsert_store({
            "id": "store-a",
            "display_name": "Ozon 莫斯科",
            "platform": "OZON",
            "credential_ref": "ozon-adapter/.env.store-a",
        })
        self.repo.upsert_store({"id": "store-b", "display_name": "第二店铺", "platform": "OZON"})
        self.repo.upsert_product({
            "id": "PRODUCT-A",
            "source_platform": "1688",
            "source_url": "D:\\private\\products\\PRODUCT-A",
            "title_cn": "保温杯",
            "title_ru": "Термокружка",
            "status": "SYNCED",
        })
        self.repo.upsert_sku({
            "id": "SKU-A",
            "product_id": "PRODUCT-A",
            "seller_sku": "SELLER-SKU-A",
        })
        self.repo.upsert_listing({
            "store_id": "store-a",
            "product_id": "PRODUCT-A",
            "sku_id": "SKU-A",
            "platform": "OZON",
            "offer_id": "OFFER-A",
            "ozon_product_id": "9001",
            "remote_title": "Remote mug",
            "remote_price": 1499.5,
            "remote_images": ["https://cdn.example.test/mug.jpg"],
            "currency": "RUB",
            "listing_status": "PARTIAL",
            "presence_status": "NOT_SEEN",
            "attributes_status": "FAILED",
            "last_sync_at": "2026-09-27T08:00:00+08:00",
        })
        with database(self.root) as db:
            db.execute(
                "UPDATE marketplace_listings SET presence_status='NOT_SEEN' WHERE store_id='store-a'"
            )
        self.repo.upsert_product({"id": "PRODUCT-B", "title_cn": "餐盒", "status": "SYNCED"})
        self.repo.upsert_sku({"id": "SKU-B", "product_id": "PRODUCT-B", "seller_sku": "SELLER-SKU-B"})
        self.repo.upsert_listing({
            "store_id": "store-b",
            "product_id": "PRODUCT-B",
            "sku_id": "SKU-B",
            "offer_id": "OFFER-B",
            "ozon_product_id": "9002",
            "listing_status": "ONLINE",
            "presence_status": "SEEN",
            "attributes_status": "SYNCED",
        })
        self.repo.upsert_product({"id": "PRODUCT-C", "title_cn": "未上架商品", "status": "DRAFT"})
        self.repo.upsert_sku({"id": "SKU-C", "product_id": "PRODUCT-C", "seller_sku": "SELLER-SKU-C"})

    @staticmethod
    def request(path, query=None):
        app = FastAPI()
        app.include_router(ozon_products.router)

        async def run():
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

            await app({
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": urlencode(query or {}).encode(),
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("test", 80),
                "root_path": "",
            }, receive, send)
            return AsgiResponse(sent)

        return asyncio.run(run())

    def test_list_returns_persisted_product_sku_and_listing_fields_without_secrets_or_paths(self):
        with patch.object(ozon_products, "ROOT", self.root):
            response = self.request("/api/erp/products", {"q": "OFFER-A"})

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json()["total"])
        self.assertEqual({
            "listing_id": response.json()["items"][0]["listing_id"],
            "product_id": "PRODUCT-A",
            "sku_id": "SKU-A",
            "sku": "SELLER-SKU-A",
            "offer_id": "OFFER-A",
            "ozon_product_id": "9001",
            "title": "Remote mug",
            "ozon_sku": None,
            "platform_skus": [],
            "store_id": "store-a",
            "store": "Ozon 莫斯科",
            "image_url": "https://cdn.example.test/mug.jpg",
            "price": 1499.5,
            "currency": "RUB",
            "listing_status": "PARTIAL",
            "presence_status": "NOT_SEEN",
            "attribute_status": "FAILED",
            "last_sync": "2026-09-27T08:00:00+08:00",
        }, response.json()["items"][0])
        self.assertNotIn("credential_ref", response.text)
        self.assertNotIn(".env.store-a", response.text)
        self.assertNotIn("D:\\\\private", response.text)

    def test_list_filters_and_paginates_marketplace_rows(self):
        with patch.object(ozon_products, "ROOT", self.root):
            filtered = self.request("/api/erp/products", {
                "store_id": "store-a",
                "listing_status": "PARTIAL",
                "presence_status": "NOT_SEEN",
                "attribute_status": "FAILED",
            })
            second_page = self.request("/api/erp/products", {"page": 2, "page_size": 1})

        self.assertEqual(["OFFER-A"], [item["offer_id"] for item in filtered.json()["items"]])
        self.assertEqual({"total": 3, "page": 2, "page_size": 1}, {
            key: second_page.json()[key] for key in ("total", "page", "page_size")
        })
        self.assertEqual(1, len(second_page.json()["items"]))

    def test_unlisted_product_keeps_one_deterministic_sku_without_multiplying_listing_rows(self):
        self.repo.upsert_sku({"id": "SKU-A2", "product_id": "PRODUCT-A", "seller_sku": "SELLER-SKU-A2"})
        self.repo.upsert_sku({"id": "SKU-C2", "product_id": "PRODUCT-C", "seller_sku": "ZZZ-SECONDARY-SKU"})
        self.repo.upsert_listing({
            "store_id": "store-b",
            "product_id": "PRODUCT-A",
            "sku_id": "SKU-A2",
            "offer_id": "OFFER-A2",
            "ozon_product_id": "9003",
        })
        with patch.object(ozon_products, "ROOT", self.root):
            unlisted = self.request("/api/erp/products", {"q": "PRODUCT-C"})
            unlisted_by_secondary_sku = self.request("/api/erp/products", {"q": "ZZZ-SECONDARY-SKU"})
            listed = self.request("/api/erp/products", {"q": "PRODUCT-A"})
            listed_by_sku = self.request("/api/erp/products", {"q": "SELLER-SKU-A2"})

        self.assertEqual(1, unlisted.json()["total"])
        self.assertEqual(("SKU-C", "SELLER-SKU-C", None), tuple(
            unlisted.json()["items"][0][key] for key in ("sku_id", "sku", "listing_id")
        ))
        self.assertEqual(1, unlisted_by_secondary_sku.json()["total"])
        self.assertEqual(("PRODUCT-C", "SKU-C"), tuple(
            unlisted_by_secondary_sku.json()["items"][0][key] for key in ("product_id", "sku_id")
        ))
        self.assertEqual(2, listed.json()["total"])
        self.assertEqual(
            {("SKU-A", "OFFER-A"), ("SKU-A2", "OFFER-A2")},
            {(item["sku_id"], item["offer_id"]) for item in listed.json()["items"]},
        )
        self.assertEqual(["OFFER-A2"], [item["offer_id"] for item in listed_by_sku.json()["items"]])

    def test_image_projection_rejects_local_and_protocol_relative_paths(self):
        unsafe_images = [
            "/Users/private/secret.png",
            "//host/share/secret.png",
            "file:///Users/private/secret.png",
            "C:\\private\\secret.png",
            "http://127.0.0.1:8999/private",
            "http://127.0.0.1。:8999/private",
            "https://localhost./private",
            "http://192.168.0.1/private",
            "ftp://cdn.example.test/mug.jpg",
        ]
        for image in unsafe_images:
            with self.subTest(image=image):
                self.repo.upsert_listing({
                    "store_id": "store-a",
                    "product_id": "PRODUCT-A",
                    "sku_id": "SKU-A",
                    "offer_id": "OFFER-A",
                    "remote_images": [image],
                })
                with patch.object(ozon_products, "ROOT", self.root):
                    response = self.request("/api/erp/products", {"q": "OFFER-A"})
                self.assertIsNone(response.json()["items"][0]["image_url"])
                self.assertNotIn(image, response.text)

    def test_image_projection_keeps_public_ozon_image(self):
        image = "https://ir.ozone.ru/s3/multimedia-1-r/c600/111.jpg"
        self.repo.upsert_listing({
            "store_id": "store-a", "product_id": "PRODUCT-A", "sku_id": "SKU-A",
            "offer_id": "OFFER-A", "remote_images": [image],
        })
        with patch.object(ozon_products, "ROOT", self.root):
            response = self.request("/api/erp/products", {"q": "OFFER-A"})
        self.assertEqual(image, response.json()["items"][0]["image_url"])

    def test_detail_keeps_product_sku_and_listing_identities_separate(self):
        with patch.object(ozon_products, "ROOT", self.root):
            response = self.request("/api/erp/products/PRODUCT-A")

        self.assertEqual(200, response.status_code)
        detail = response.json()
        self.assertEqual("PRODUCT-A", detail["product_id"])
        self.assertEqual([{"sku_id": "SKU-A", "sku": "SELLER-SKU-A"}], detail["skus"])
        self.assertEqual("OFFER-A", detail["listings"][0]["offer_id"])
        self.assertEqual("9001", detail["listings"][0]["ozon_product_id"])
        self.assertNotEqual(detail["product_id"], detail["skus"][0]["sku_id"])
        self.assertNotEqual(detail["skus"][0]["sku_id"], detail["listings"][0]["listing_id"])

    def test_missing_detail_is_404(self):
        with patch.object(ozon_products, "ROOT", self.root):
            response = self.request("/api/erp/products/UNKNOWN")
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()
