from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from backend.repositories.erp import ErpRepository


def _items(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = response.get("items")
    if not isinstance(value, list):
        result = response.get("result")
        value = result.get("items") if isinstance(result, Mapping) else result
    return [dict(item) for item in (value or []) if isinstance(item, Mapping)]


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _safe_error(exc: Exception) -> str:
    message = re.sub(
        r"(?i)(client-id|api-key|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=<hidden>",
        str(exc),
    )
    return message[:500]


class OzonCatalogSyncService:
    def __init__(self, repository: ErpRepository, client: Any, *, detail_batch_size: int = 100):
        self.repo = repository
        self.client = client
        self.detail_batch_size = max(1, min(int(detail_batch_size), 100))

    def _stop_if_requested(self, job_id: str) -> bool:
        if not self.repo.sync_job_cancel_requested(job_id):
            return False
        self.repo.update_sync_job(
            job_id, status="CANCELLED", stage="CANCELLED",
            finished_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            message="商品同步已取消",
        )
        return True

    def _call(self, job_id: str, store_id: str, stage: str, endpoint: str, page: int, function):
        started = time.perf_counter()
        try:
            response = function()
        except Exception as exc:
            self.repo.add_sync_job_log(
                job_id, store_id, stage=stage, endpoint=endpoint, page=page,
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__, error_message=_safe_error(exc),
            )
            raise
        rows = _items(response)
        self.repo.add_sync_job_log(
            job_id, store_id, stage=stage, endpoint=endpoint, page=page,
            duration_ms=int((time.perf_counter() - started) * 1000), items_received=len(rows),
        )
        return response, rows

    def _finish(
        self,
        job_id: str,
        store_id: str,
        seen_entities: set[str],
        failed_entities: set[str],
        errors: dict[str, str],
        *,
        stage: str = "COMPLETED",
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        self.repo.record_sync_job_items(
            job_id,
            [
                (
                    entity_id,
                    "FAILED" if entity_id in failed_entities else "SUCCEEDED",
                    errors.get(entity_id),
                )
                for entity_id in sorted(seen_entities)
            ],
        )
        total = len(seen_entities)
        failure_count = len(failed_entities)
        success_count = total - failure_count
        final_status = status or ("PARTIAL" if failure_count else "SUCCEEDED")
        final_error = None if not failure_count else f"{failure_count} product sync items failed"
        if final_status == "SUCCEEDED":
            self.repo.reconcile_store_listings(store_id, job_id)
        self.repo.update_store_last_sync(store_id, error=final_error)
        self.repo.add_audit_log(
            "OZON_PRODUCT_SYNC_COMPLETED",
            "store",
            store_id,
            {
                "job_id": job_id,
                "status": final_status,
                "total": total,
                "success_count": success_count,
                "failure_count": failure_count,
            },
        )
        self.repo.update_sync_job(
            job_id,
            status=final_status,
            stage=stage,
            progress=total,
            total=total,
            success_count=success_count,
            failure_count=failure_count,
            finished_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            message=message or f"已同步 {success_count} 个商品",
            error=final_error,
        )

    def run(self, job_id: str, store_id: str) -> None:
        catalog: list[dict[str, Any]] = []
        seen_entities: set[str] = set()
        failed_entities: set[str] = set()
        errors: dict[str, str] = {}
        product_entities: dict[str, str] = {}
        offer_entities: dict[str, str] = {}
        seen_cursors: set[str] = set()
        last_id = ""
        page = 0
        self.repo.update_sync_job(job_id, status="RUNNING", stage="PRODUCT_LIST", message="正在读取商品列表")
        if self._stop_if_requested(job_id):
            return
        try:
            while True:
                page += 1
                response, rows = self._call(
                    job_id, store_id, "PRODUCT_LIST", self.client.PRODUCT_LIST_ENDPOINT, page,
                    lambda: self.client.list_products(limit=1000, last_id=last_id),
                )
                valid_rows = []
                for row_number, item in enumerate(rows, start=1):
                    remote_id = str(item.get("product_id") or item.get("id") or "").strip()
                    offer_id = str(item.get("offer_id") or "").strip()
                    entity_id = (
                        f"product:{remote_id}"
                        if remote_id
                        else f"offer:{offer_id}"
                        if offer_id
                        else f"page:{page}:row:{row_number}"
                    )
                    seen_entities.add(entity_id)
                    if not remote_id or not offer_id:
                        failed_entities.add(entity_id)
                        errors[entity_id] = "Malformed product list row"
                        continue
                    valid_rows.append(item)
                    product_entities[remote_id] = entity_id
                    offer_entities[offer_id] = entity_id
                saved = self.repo.upsert_catalog_page(store_id, valid_rows, sync_id=job_id)
                catalog.extend(valid_rows)
                result = response.get("result") if isinstance(response.get("result"), Mapping) else {}
                next_id = str(result.get("last_id") or response.get("last_id") or "")
                self.repo.add_sync_job_log(
                    job_id, store_id, stage="PRODUCT_LIST", endpoint=self.client.PRODUCT_LIST_ENDPOINT,
                    page=page, items_received=len(rows), items_saved=saved, last_id=next_id,
                )
                self.repo.update_sync_job(
                    job_id,
                    progress=len(seen_entities),
                    total=len(seen_entities),
                    success_count=len(seen_entities) - len(failed_entities),
                    failure_count=len(failed_entities),
                    message=f"正在同步商品 {len(seen_entities)}",
                )
                if self._stop_if_requested(job_id):
                    return
                if not rows or not next_id:
                    break
                if next_id == last_id or next_id in seen_cursors:
                    raise RuntimeError("Ozon product pagination cursor did not advance")
                seen_cursors.add(next_id)
                last_id = next_id
        except Exception as exc:
            entity_id = f"page:{page}"
            seen_entities.add(entity_id)
            failed_entities.add(entity_id)
            errors[entity_id] = _safe_error(exc)
            self._finish(
                job_id,
                store_id,
                seen_entities,
                failed_entities,
                errors,
                stage="PRODUCT_LIST",
                status="PARTIAL" if catalog else "FAILED",
                message=f"商品列表同步{'部分完成' if catalog else '失败'}",
            )
            return

        product_ids = []
        for item in catalog:
            remote_id = str(item.get("product_id") or item.get("id") or "").strip()
            if remote_id.isdigit():
                product_ids.append(int(remote_id))
            else:
                entity_id = product_entities[remote_id]
                failed_entities.add(entity_id)
                errors.setdefault(entity_id, "Product ID is not numeric")
        self.repo.update_sync_job(job_id, stage="PRODUCT_DETAILS", progress=0, total=len(product_ids), message="正在补充商品详情")
        for batch, group in enumerate(_chunks(product_ids, self.detail_batch_size), start=1):
            try:
                _, rows = self._call(
                    job_id, store_id, "PRODUCT_DETAILS", self.client.PRODUCT_INFO_ENDPOINT, batch,
                    lambda group=group: self.client.get_product_info(group),
                )
                self.repo.update_listing_details(store_id, rows)
                returned = {
                    str(item.get("id") or item.get("product_id") or "").strip()
                    for item in rows
                }
                for remote_id in (str(value) for value in group):
                    if remote_id not in returned:
                        entity_id = product_entities[remote_id]
                        failed_entities.add(entity_id)
                        errors.setdefault(
                            entity_id, "Ozon product detail response omitted requested product"
                        )
            except Exception as exc:
                safe_error = _safe_error(exc)
                for remote_id in (str(value) for value in group):
                    entity_id = product_entities[remote_id]
                    failed_entities.add(entity_id)
                    errors.setdefault(entity_id, safe_error)
            done = min(batch * self.detail_batch_size, len(product_ids))
            self.repo.update_sync_job(
                job_id,
                progress=done,
                success_count=len(seen_entities) - len(failed_entities),
                failure_count=len(failed_entities),
                message=f"正在补充商品详情 {done} / {len(product_ids)}",
            )
            if self._stop_if_requested(job_id):
                return

        offer_ids = list(offer_entities)
        self.repo.update_sync_job(job_id, stage="ATTRIBUTES", progress=0, total=len(offer_ids), message="正在同步商品属性")
        for batch, group in enumerate(_chunks(offer_ids, 100), start=1):
            try:
                _, rows = self._call(
                    job_id, store_id, "ATTRIBUTES", self.client.PRODUCT_ATTRIBUTES_ENDPOINT, batch,
                    lambda group=group: self.client.get_product_attributes(offer_ids=group),
                )
                persisted = self.repo.persist_listing_attributes(store_id, rows)
                missing = [offer_id for offer_id in group if offer_id not in persisted]
                self.repo.mark_attributes_status(
                    store_id, missing, "FAILED", "Ozon attribute response omitted requested offer"
                )
                for offer_id in missing:
                    entity_id = offer_entities[offer_id]
                    failed_entities.add(entity_id)
                    errors.setdefault(
                        entity_id, "Ozon attribute response omitted requested offer"
                    )
            except Exception as exc:
                safe_error = _safe_error(exc)
                self.repo.mark_attributes_status(store_id, group, "FAILED", safe_error)
                for offer_id in group:
                    entity_id = offer_entities[offer_id]
                    failed_entities.add(entity_id)
                    errors.setdefault(entity_id, safe_error)
            done = min(batch * 100, len(offer_ids))
            self.repo.update_sync_job(job_id, progress=done, message=f"正在同步商品属性 {done} / {len(offer_ids)}")
            if self._stop_if_requested(job_id):
                return

        if self._stop_if_requested(job_id):
            return

        self._finish(
            job_id,
            store_id,
            seen_entities,
            failed_entities,
            errors,
            message=f"已同步 {len(seen_entities) - len(failed_entities)} 个商品",
        )
