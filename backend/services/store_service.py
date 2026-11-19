"""Validate an Ozon store with account identity and product-read access."""
from __future__ import annotations

import json
from typing import Any

from backend.repositories.erp import ErpRepository, now
from backend.db.erp import database


class SellerIdentityError(RuntimeError):
    pass


class ProductReadPermissionError(RuntimeError):
    pass


class StoreService:
    def __init__(self, repository: ErpRepository, account_client: Any, product_client: Any) -> None:
        self.repository = repository
        self.account_client = account_client
        self.product_client = product_client

    def read_store(self, store_id: str) -> dict[str, Any]:
        store = self.repository.get_store(store_id)
        if store is None:
            raise KeyError(store_id)
        allowed = None
        for audit in reversed(self.repository.list_audit_logs(store_id)):
            if audit["action"] == "STORE_CREDENTIAL_VALIDATION" and audit["entity_type"] == "store":
                evidence = json.loads(audit["details_json"])
                allowed = True if evidence.get("product_read_allowed") is True else None
                break
        with database(self.repository.root, readonly=True) as db:
            listing_count = db.execute("SELECT COUNT(*) FROM marketplace_listings WHERE store_id=?", (store_id,)).fetchone()[0]
            analytics_time = db.execute("SELECT MAX(fetched_at) FROM analytics_snapshots WHERE store_id=? AND organization_id=?",
                                        (store_id, store["organization_id"])).fetchone()[0]
        return {
            **{key: store[key] for key in ("id", "display_name", "platform", "enabled", "seller_id",
                                          "company_name", "connection_status", "last_connected_at")},
            # SellerInfo has no Seller ID; legacy rows may contain an inferred Client-Id.
            "seller_id": None if store["platform"] == "OZON" else store["seller_id"],
            "product_read_allowed": allowed,
            "last_product_sync_at": store["last_sync_at"],
            "product_count": self.repository.list_products(store_id=store_id, page_size=1)["total"],
            "currency": store["currency"],
            "listing_count": listing_count,
            "analytics_fetched_at": analytics_time,
        }

    def _record_probe(self, store_id: str, allowed: bool | None) -> None:
        self.repository.add_audit_log("STORE_CREDENTIAL_VALIDATION", "store", store_id,
                                     {"product_read_allowed": allowed})

    def validate_credentials(self, store_id: str) -> dict[str, Any]:
        store = self.repository.get_store(store_id)
        if store is None:
            raise KeyError(store_id)
        try:
            company = self.account_client.get_seller_info()["company"]
            company_name = company["name"].strip()
            if not company_name:
                raise ValueError("missing seller identity")
        except Exception:
            self._record_probe(store_id, None)
            self.repository.upsert_store({**store, "connection_status": "FAILED", "last_error": "Seller identity validation failed"})
            raise SellerIdentityError("Seller identity validation failed") from None
        try:
            response = self.product_client.list_products(limit=1)
            if not isinstance(response, dict) or not isinstance(response.get("result"), dict) or not isinstance(response["result"].get("items"), list):
                raise ValueError("invalid product-list response")
        except Exception:
            self._record_probe(store_id, None)
            self.repository.upsert_store({**store, "connection_status": "FAILED", "last_error": "Product Read permission validation failed"})
            raise ProductReadPermissionError("Product Read permission validation failed") from None
        stamp = now()
        self.repository.upsert_store({
            **store,
            # The official seller-info response has no seller_id; Client-Id is an auth header, not a returned seller ID.
            "seller_id": None,
            "company_name": company_name,
            "currency": company.get("currency") or store.get("currency") or "RUB",
            "connection_status": "CONNECTED",
            "last_connected_at": stamp,
            "last_error": None,
        })
        self._record_probe(store_id, True)
        return {
            "store_id": store_id,
            "seller_id": None,
            "company_name": company_name,
            "connection_status": "CONNECTED",
            "product_read_allowed": True,
            "last_connected_at": stamp,
        }
