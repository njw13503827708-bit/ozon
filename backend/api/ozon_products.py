from __future__ import annotations

import os
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.integrations.ozon.product_client import OzonProductClient
from backend.integrations.ozon.credential_store import CredentialStore
from backend.repositories.erp import ErpRepository
from backend.services.ozon_catalog_sync_service import OzonCatalogSyncService, _safe_error


ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/erp", tags=["erp-ozon-products"])


def queue_catalog_sync(repository: ErpRepository, store_id: str, worker: Callable[[str, str], None]) -> dict[str, Any]:
    job_id, created = repository.create_or_get_active_sync_job("OZON_PRODUCT_SYNC", store_id)
    if created:
        def guarded_worker() -> None:
            try:
                worker(job_id, store_id)
            except Exception as exc:
                repository.update_sync_job(
                    job_id,
                    status="FAILED",
                    stage="FAILED",
                    finished_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                    message="商品同步失败",
                    error=_safe_error(exc),
                )

        threading.Thread(
            target=guarded_worker,
            name=f"ozon-catalog-{job_id[:8]}",
            daemon=True,
        ).start()
    job = repository.get_sync_job(job_id) or {}
    return {"job_id": job_id, "status": job.get("status", "QUEUED"), "reused": not created}


def _store_credentials(store_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    from workbench_stores import load_registry

    registry = load_registry(ROOT)
    shop = next((item for item in registry.get("shops") or [] if str(item.get("id")) == store_id), None)
    if shop is None:
        raise HTTPException(status_code=404, detail="未找到店铺")
    if not shop.get("enabled", True):
        raise HTTPException(status_code=409, detail="店铺已停用")
    try:
        values = CredentialStore(ROOT).read(shop)
    except OSError:
        raise HTTPException(status_code=409, detail="店铺凭证文件不可读（运行身份与文件属主不匹配），请在主电脑重新保存凭证")
    client_id = values.get(str(shop.get("client_id_env") or ""), "")
    api_key = values.get(str(shop.get("api_key_env") or ""), "")
    if not client_id or not api_key:
        raise HTTPException(status_code=409, detail="店铺 API 凭证未配置")
    return shop, {"client_id": client_id, "api_key": api_key}


def require_host_device(request: Request) -> None:
    operator = getattr(request.state, "operator", None) or {}
    if not operator.get("is_host_device"):
        raise HTTPException(status_code=403, detail="只有主电脑可以同步店铺商品")


@router.post("/stores/{store_id}/products/sync", status_code=202)
def start_product_sync(
    store_id: str, _: None = Depends(require_host_device)
) -> dict[str, Any]:
    shop, credentials = _store_credentials(store_id)
    repository = ErpRepository(ROOT)
    repository.upsert_store({
        "id": store_id,
        "display_name": shop.get("display_name") or store_id,
        "currency": shop.get("default_currency_code") or "RUB",
        "enabled": True,
        "connection_status": str(shop.get("validation_status") or "NEEDS_VALIDATION").upper(),
        "credential_ref": f"ozon-adapter/.env.{store_id}",
        "last_connected_at": shop.get("last_validated_at"),
        "last_error": shop.get("last_validation_error"),
        # Registry refresh must not replace identity/probe state owned by Store validation.
        **{key: value for key, value in (repository.get_store(store_id) or {}).items()
           if key in {"company_name", "seller_id", "connection_status", "last_connected_at", "last_error"}},
    })
    client = OzonProductClient(
        credentials["client_id"], credentials["api_key"],
        base_url=os.getenv("OZON_API_BASE_URL"),
    )
    service = OzonCatalogSyncService(repository, client)
    return queue_catalog_sync(repository, store_id, service.run)


@router.get("/sync-jobs/{job_id}")
def get_product_sync_job(job_id: str) -> dict[str, Any]:
    job = ErpRepository(ROOT).get_sync_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="未找到同步任务")
    return {"id": job.pop("id"), **job}


@router.post("/sync-jobs/{job_id}/cancel", status_code=202)
def request_product_sync_cancel(
    job_id: str, _: None = Depends(require_host_device)
) -> dict[str, Any]:
    job = ErpRepository(ROOT).request_sync_job_cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="未找到同步任务")
    if not job["cancel_requested"]:
        raise HTTPException(status_code=409, detail="同步任务已结束")
    return {"job_id": job_id, "status": job["status"], "cancel_requested": True}


@router.get("/stores/{store_id}/products")
def list_store_products(store_id: str, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    repository = ErpRepository(ROOT)
    result = repository.list_products(store_id=store_id, page=page, page_size=page_size)
    for item in result["items"]:
        raw_images = item.pop("remote_images_json", None)
        try:
            item["images"] = json.loads(raw_images) if raw_images else []
        except json.JSONDecodeError:
            item["images"] = []
    result["sync_job"] = repository.latest_sync_job(store_id, "OZON_PRODUCT_SYNC")
    return result


@router.get("/products")
def list_products(
    q: str = "", store_id: str = "", listing_status: str = "",
    presence_status: str = "", attribute_status: str = "", page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    return ErpRepository(ROOT).list_product_center(
        q=q,
        store_id=store_id,
        listing_status=listing_status,
        presence_status=presence_status,
        attribute_status=attribute_status,
        page=page,
        page_size=page_size,
    )


@router.get("/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    product = ErpRepository(ROOT).get_product_center_detail(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="未找到商品")
    return product
