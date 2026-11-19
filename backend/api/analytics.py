"""Local analytics reads and host-gated, fixed-contract Ozon read jobs."""
import threading
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.ozon_products import _store_credentials, require_host_device
from backend.integrations.ozon.analytics_client import OzonAnalyticsClient, EXTRA_METRICS, validate_dates
from backend.integrations.ozon.base_client import OzonApiError
from backend.repositories.analytics import AnalyticsRepository
from backend.repositories.erp import now
from backend.services.analytics_service import AnalyticsService

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/erp/stores", tags=["erp-analytics"])


class RefreshRequest(BaseModel):
    date_from: str
    date_to: str
    include_extended: bool = True


@router.get("/{store_id}/analytics")
def read_analytics(store_id: str, date_from: str, date_to: str, metric: str = "revenue", q: str = "", page: int = 1, page_size: int = 50):
    try:
        return AnalyticsService(AnalyticsRepository(ROOT), None).read(
            store_id, date_from, date_to, metric=metric, q=q, page=page, page_size=page_size)
    except ValueError:
        raise HTTPException(422, "请检查日期区间、指标与分页参数") from None
    except KeyError:
        raise HTTPException(404, "未找到店铺") from None


@router.post("/{store_id}/analytics/refresh", status_code=202)
def refresh_analytics(store_id: str, body: RefreshRequest, _: None = Depends(require_host_device)):
    try:
        validate_dates(body.date_from, body.date_to)
    except ValueError:
        raise HTTPException(422, "请检查分析日期区间") from None
    repo = AnalyticsRepository(ROOT)
    store = repo.erp.get_store(store_id)
    if store is None:
        raise HTTPException(404, "未找到店铺")
    if not store["enabled"] or store["platform"] != "OZON":
        raise HTTPException(409, "仅支持已启用的 Ozon 店铺")
    _, credentials = _store_credentials(store_id)
    client = OzonAnalyticsClient(**credentials)
    job, created = repo.erp.create_or_get_active_sync_job(
        "OZON_ANALYTICS_SYNC", store_id,
        entity_id=f"{body.date_from}:{body.date_to}:{int(body.include_extended)}")
    if created:
        def worker():
            repo.erp.update_sync_job(job, status="RUNNING", stage="ANALYTICS", message="正在读取店铺运营数据")
            try:
                service = AnalyticsService(repo, client)
                service.refresh(store_id, body.date_from, body.date_to,
                                extra_metrics=EXTRA_METRICS if body.include_extended else (), job_id=job)
                data = service.read(store_id, body.date_from, body.date_to)
                repo.erp.update_sync_job(job, status="SUCCEEDED" if data["availability"] == "AVAILABLE" else "PARTIAL",
                                         stage="DONE", finished_at=now(), success_count=data["total"],
                                         message="分析读取完成；部分指标可能受权限或订阅限制")
            except InterruptedError:
                repo.erp.update_sync_job(job, status="CANCELLED", stage="CANCELLED", finished_at=now(), message="分析读取已取消")
            except Exception as exc:
                message = "分析读取失败；保留上次成功数据"
                if isinstance(exc, OzonApiError):
                    status = exc.status_code
                    if status in {401, 403}:
                        message = "分析读取未获授权，请检查 API 凭证或订阅权限；保留已有数据"
                    elif status is None:
                        message = "分析服务连接失败，请检查网络后重试；保留已有数据"
                    elif status == 429:
                        message = "分析请求受限（HTTP 429），请稍后重试；保留已有数据"
                    else:
                        message = f"Ozon 分析请求失败（HTTP {status}）；保留已有数据"
                elif isinstance(exc, ValueError):
                    message = "分析返回的数据格式未通过验证；保留已有数据"
                elif isinstance(exc, sqlite3.Error):
                    message = "分析数据保存失败，请检查数据库可写性；保留已有数据"
                logging.error("Analytics job=%s failed type=%s http_status=%s",
                              job, type(exc).__name__, getattr(exc, "status_code", None))
                repo.erp.update_sync_job(job, status="FAILED", stage="FAILED", finished_at=now(), message=message, error=message)
        threading.Thread(target=worker, name=f"analytics-{job[:8]}", daemon=True).start()
    return {"job_id": job, "reused": not created, "ozon_write_api_calls": 0, "inventory_api_calls": 0}
