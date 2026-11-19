"""Store Center reads and host-only, read-only Ozon credential probes."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.ozon_products import _store_credentials, require_host_device
from backend.integrations.ozon.account_client import OzonAccountClient
from backend.integrations.ozon.product_client import OzonProductClient
from backend.repositories.erp import ErpRepository
from backend.services.store_service import StoreService

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/erp/stores", tags=["erp-stores"])


def _reader():
    return StoreService(ErpRepository(ROOT), None, None)


@router.get("")
def list_stores(q: str = "", connection_status: str = "", page: int = Query(1, ge=1), page_size: int = Query(200, ge=1, le=200)):
    service = _reader()
    all_stores = service.repository.list_stores()
    stores = [store for store in all_stores
              if (not connection_status or store["connection_status"] == connection_status)
              and q.casefold() in str(store["display_name"]).casefold()]
    return {"items": [service.read_store(store["id"]) for store in stores[(page - 1) * page_size:page * page_size]],
            "total": len(stores), "unfiltered_total": len(all_stores), "page": page, "page_size": page_size}


@router.get("/{store_id}")
def get_store(store_id: str):
    try:
        return _reader().read_store(store_id)
    except KeyError:
        raise HTTPException(404, "未找到店铺") from None


@router.post("/{store_id}/validate")
def validate_store(store_id: str, _: None = Depends(require_host_device)):
    repository = ErpRepository(ROOT)
    store = repository.get_store(store_id)
    if store is None:
        raise HTTPException(404, "未找到店铺")
    if store["platform"] != "OZON" or not store["enabled"]:
        raise HTTPException(409, "仅支持验证已启用的 Ozon 店铺")
    try:
        if store["credential_ref"] != f"ozon-adapter/.env.{store_id}":
            raise ValueError("Credential reference mismatch")
        _, credentials = _store_credentials(store_id)
        service = StoreService(repository, OzonAccountClient(**credentials),
                               OzonProductClient(**credentials))
    except Exception:
        StoreService(repository, None, None)._record_probe(store_id, None)
        repository.upsert_store({**store, "connection_status": "FAILED", "last_error": "Credential read failed"})
        raise HTTPException(409, "无法读取店铺凭证，请检查店铺设置") from None
    try:
        service.validate_credentials(store_id)
    except Exception:
        raise HTTPException(502, "店铺验证未完成，请检查连接与读取权限后重试") from None
    return service.read_store(store_id)
