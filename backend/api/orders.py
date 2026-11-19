"""FBS/rFBS order reads. Explicit authorization required for new outbound reads."""
import os
import threading
from pathlib import Path

from fastapi import APIRouter, Request, Query
from pydantic import BaseModel, ConfigDict

from backend.api.optimization import handled, operator, period
from backend.api.ozon_products import _store_credentials
from backend.integrations.ozon.base_client import OzonApiError
from backend.integrations.ozon.order_client import OzonOrderClient
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.order_sync_service import OrderSyncService, owned_store

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/api/erp/stores', tags=['read-only-orders'])


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date_from: str
    date_to: str


def order_read_authorized():
    return os.getenv('CAF_ORDER_READ_APPROVED') == '1'


@router.get('/{store_id}/orders')
@handled
def read_orders(store_id: str, request: Request, date_from: str = '', date_to: str = '', q: str = '',
                status: str = '', page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    operator(request)
    date_from, date_to = period(date_from, date_to)
    return {**OrderSyncService(ROOT).read(store_id, date_from, date_to, q=q, status=status, page=page, page_size=page_size),
            'read_authorized': order_read_authorized(), 'platform_write_calls': 0}


@router.get('/{store_id}/orders/jobs/{job_id}')
@handled
def read_order_job(store_id: str, job_id: str, request: Request):
    operator(request)
    owned_store(ROOT, store_id)
    job = LocalOperationsRepository(ROOT).job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job['store_id'] != store_id or job['kind'] != 'ORDERS':
        raise PermissionError('读取任务不属于所选店铺')
    return job


@router.post('/{store_id}/orders/sync', status_code=202)
@handled
def sync_orders(store_id: str, body: SyncRequest, request: Request):
    operator(request, mutation=True)
    owned_store(ROOT, store_id)
    date_from, date_to = period(body.date_from, body.date_to)
    if not order_read_authorized():
        raise PermissionError('新增FBS/rFBS订单读取尚未批准；本地查询和商品内容编辑仍可使用')
    shop, credentials = _store_credentials(store_id)
    if str(shop.get('validation_status') or '').lower() != 'connected':
        raise RuntimeError('店铺凭证尚未连接验证或已变更，请先核对自有店铺连接')
    repo = LocalOperationsRepository(ROOT)
    job, created = repo.start_job('ORDERS', store_id, f'{date_from}:{date_to}')
    if created:
        def worker():
            try:
                OrderSyncService(ROOT, OzonOrderClient(**credentials)).run(store_id, date_from, date_to, job['id'])
            except InterruptedError:
                repo.update_job(job['id'], status='CANCELLED', message='订单读取取消，保留上次完整缓存')
            except Exception as exc:
                status = exc.status_code if isinstance(exc, OzonApiError) else None
                message = ('订单读取未获平台权限，请检查API角色；不继续重试' if status in {401, 403}
                           else f'订单读取失败（HTTP {status}），保留上次完整缓存' if status
                           else '订单读取/保存失败，保留上次完整缓存；请检查网络或响应格式后手工重试')
                repo.update_job(job['id'], status='FAILED', message=message)
        threading.Thread(target=worker, name=f'orders-{job["id"][:8]}', daemon=True).start()
    return {'job_id': job['id'], 'reused': not created, 'platform_write_calls': 0}


@router.get('/{store_id}/orders/{posting_number}')
@handled
def read_order_detail(store_id: str, posting_number: str, request: Request, date_from: str = '', date_to: str = ''):
    operator(request)
    owned_store(ROOT, store_id)
    date_from, date_to = period(date_from, date_to)
    snapshot = LocalOperationsRepository(ROOT).snapshot('ORDERS', store_id, date_from, date_to)
    found = next((row for row in snapshot['data']['rows'] if row['posting_number'] == posting_number
                  and (row['date'] is None or date_from <= row['date'] <= date_to)), None) if snapshot else None
    if found is None:
        raise KeyError(posting_number)
    return {'store_id': store_id, 'posting': found, 'source': snapshot['data']['source'],
            'fetched_at': snapshot['fetched_at'], 'snapshot_id': snapshot['id'], 'platform_write_calls': 0}
