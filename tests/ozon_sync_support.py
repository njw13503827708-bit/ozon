from __future__ import annotations
from backend.db.erp import initialize as initialize_erp_test_database

import tempfile
from pathlib import Path

from backend.repositories.erp import ErpRepository


class CatalogClient:
    PRODUCT_LIST_ENDPOINT = "/v3/product/list"
    PRODUCT_INFO_ENDPOINT = "/v3/product/info/list"
    PRODUCT_ATTRIBUTES_ENDPOINT = "/v4/product/info/attributes"

    def __init__(
        self,
        pages,
        *,
        fail_cursor=None,
        attributes_error=None,
        omitted_detail_ids=(),
        failed_attribute_offers=(),
    ):
        self.pages = pages
        self.fail_cursor = fail_cursor
        self.attributes_error = attributes_error
        self.omitted_detail_ids = {int(value) for value in omitted_detail_ids}
        self.failed_attribute_offers = {str(value) for value in failed_attribute_offers}
        self.list_calls = []

    def list_products(self, *, limit=1000, last_id="", visibility="ALL"):
        self.list_calls.append((limit, last_id))
        if last_id == self.fail_cursor:
            raise RuntimeError("page failed")
        items, next_id = self.pages[last_id]
        return {"result": {"items": items, "last_id": next_id}}

    def get_product_info(self, product_ids):
        return {"items": [
            {
                "id": int(product_id),
                "offer_id": f"offer-{product_id}",
                "name": f"Product {product_id}",
                "sku": int(product_id) + 10_000,
                "price": "99.50",
                "currency_code": "RUB",
                "images": [f"https://img/{product_id}.jpg"],
            }
            for product_id in product_ids
            if int(product_id) not in self.omitted_detail_ids
        ]}

    def get_product_attributes(self, offer_ids=(), product_ids=()):
        if self.attributes_error:
            raise self.attributes_error
        if self.failed_attribute_offers.intersection(str(value) for value in offer_ids):
            raise RuntimeError("Client-Id=client-secret Api-Key=api-secret")
        return {"result": [{"offer_id": offer_id} for offer_id in offer_ids]}


class SyncCase:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        initialize_erp_test_database(self.root)
        self.repo = ErpRepository(self.root)
        self.repo.upsert_store({
            "id": "store-1",
            "display_name": "Store 1",
            "credential_ref": "ozon-adapter/.env.store-1",
            "connection_status": "CONNECTED",
        })

    def tearDown(self):
        self.temp.cleanup()

    def create_job(self):
        return self.repo.create_sync_job("OZON_PRODUCT_SYNC", "store-1")


def products(start, count):
    return [
        {
            "product_id": start + offset,
            "offer_id": f"offer-{start + offset}",
            "visibility": "VISIBLE",
            "has_fbo_stocks": offset % 2 == 0,
            "has_fbs_stocks": offset % 2 == 1,
            "archived": False,
        }
        for offset in range(count)
    ]
