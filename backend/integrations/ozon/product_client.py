from __future__ import annotations

from typing import Any, Iterable

from . import ozon_endpoints
from .base_client import OzonApiError as OzonProductApiError, OzonBaseClient


class OzonProductClient(OzonBaseClient):
    PRODUCT_LIST_ENDPOINT = ozon_endpoints.PRODUCT_LIST
    PRODUCT_INFO_ENDPOINT = ozon_endpoints.PRODUCT_INFO_LIST
    PRODUCT_ATTRIBUTES_ENDPOINT = ozon_endpoints.PRODUCT_ATTRIBUTES

    def list_products(self, *, limit: int = 1000, last_id: str = "", visibility: str = "ALL") -> dict[str, Any]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("Product page size must be between 1 and 1000")
        return self._post_json(self.PRODUCT_LIST_ENDPOINT, {
            "filter": {"visibility": visibility},
            "last_id": last_id,
            "limit": int(limit),
        })

    def get_product_info(self, product_ids: Iterable[int]) -> dict[str, Any]:
        values = [int(value) for value in product_ids]
        if not values:
            raise ValueError("At least one product ID is required")
        return self._post_json(self.PRODUCT_INFO_ENDPOINT, {"offer_id": [], "product_id": values, "sku": []})

    def get_product_attributes(self, offer_ids: Iterable[str] = (), product_ids: Iterable[int] = ()) -> dict[str, Any]:
        offers = [str(value).strip() for value in offer_ids if str(value).strip()]
        products = [int(value) for value in product_ids]
        if not offers and not products:
            raise ValueError("At least one product ID or offer ID is required")
        return self._post_json(self.PRODUCT_ATTRIBUTES_ENDPOINT, {
            "filter": {"offer_id": offers, "product_id": products, "visibility": "ALL"},
            "limit": min(100, len(offers) + len(products)),
            "sort_dir": "ASC",
        })
