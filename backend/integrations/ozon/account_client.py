"""Seller-account identity read from Ozon's seller-info operation."""
from __future__ import annotations

from typing import Any

from . import ozon_endpoints
from .base_client import OzonApiError, OzonBaseClient


class OzonAccountClient(OzonBaseClient):
    def get_seller_info(self) -> dict[str, Any]:
        response = self._post_json(ozon_endpoints.SELLER_INFO, None)
        company = response.get("company")
        if not isinstance(company, dict) or not isinstance(company.get("name"), str) or not company["name"].strip():
            raise OzonApiError(ozon_endpoints.SELLER_INFO, "seller company identity missing")
        return response
