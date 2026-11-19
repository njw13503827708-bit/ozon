"""Offline review and authorization records for the batch upload approval gate.

This module deliberately has no Ozon client or network dependency.  Its only
write is the local authorization record created after an exact hash match.
"""
from __future__ import annotations

import hashlib
import asyncio
import hmac
import json
import math
import os
import re
import threading
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Legacy script and package callers must use the same reentrant lock owner.
if __name__ in {'scripts.batch_upload_approval','batch_upload_approval'}:
    sys.modules.setdefault('scripts.batch_upload_approval',sys.modules[__name__])
    sys.modules.setdefault('batch_upload_approval',sys.modules[__name__])


READY_FOR_BATCH_REVIEW = "READY_FOR_BATCH_REVIEW"
BLOCKED_BEFORE_UPLOAD = "BLOCKED_BEFORE_UPLOAD"
AWAITING_BATCH_UPLOAD_CONFIRMATION = "AWAITING_BATCH_UPLOAD_CONFIRMATION"
UPLOAD_AUTHORIZED = "UPLOAD_AUTHORIZED"


class BatchUploadBusy(RuntimeError):
    pass


_held_upload_lock = ContextVar("held_upload_lock", default=None)


@contextmanager
def batch_upload_lock(root: Path, *, wait_seconds: float = 0):
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    owner = (str(root.resolve()), threading.get_ident(), id(task))
    if _held_upload_lock.get() == owner:
        yield
        return
    # ponytail: one project-wide lock; per-batch locks if throughput matters.
    path = root / "logs" / ".batch-upload-approval.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        deadline=time.monotonic()+wait_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic()>=deadline:
                    raise BatchUploadBusy("Batch upload or protected edit is in progress") from exc
                time.sleep(.05)
        try:
            token = _held_upload_lock.set(owner)
            yield
        finally:
            _held_upload_lock.reset(token)
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _invalidate_authorization(root: Path, batch_id: str, reason: str) -> bool:
    path = authorization_path(root, batch_id)
    record = _load_json(path)
    if not record:
        return False
    if record.get("status") == "invalidated":
        return True
    record.update(status="invalidated", invalidated_at=datetime.now(timezone.utc).isoformat(), invalidation_reason=reason)
    try:
        from scripts.pipeline_runtime import write_json_atomic
    except ModuleNotFoundError:
        from pipeline_runtime import write_json_atomic
    write_json_atomic(path, record)
    return True


def invalidate_batch_upload_authorization(root: Path, batch_id: str, reason: str) -> bool:
    """Retain the complete approval audit; callers supply a non-secret reason code."""
    with batch_upload_lock(root):
        return _invalidate_authorization(root, batch_id, reason)


@contextmanager
def protected_batch_mutation(root: Path, product_id: str | None, reason: str):
    """Exclude submission until successful edits and their invalidation are durable."""
    with batch_upload_lock(root):
        records = []
        for path in (root / "batches").glob("*/upload-authorization.json"):
            record = _load_json(path)
            if record.get("status") != "authorized" or (product_id and product_id not in record.get("product_ids", [])):
                continue
            batch = _load_json(path.with_name("batch.json"))
            records.append((path.parent.name, batch, compute_batch_review_hash(root, batch)))
        successful = False
        try:
            yield
            successful = True
        finally:
            for batch_id, batch, before_hash in records:
                # Also close partial mutations when a later log/projection write fails.
                if (successful and product_id) or compute_batch_review_hash(root, batch) != before_hash:
                    _invalidate_authorization(root, batch_id, reason)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_artifacts(root: Path, product_id: str) -> dict[str, dict[str, Any]]:
    product = root / "products" / product_id
    return {
        "status": _load_json(product / "status.json"),
        "source": _load_json(product / "input" / "source.json"),
        "category_selection": _load_json(product / "input" / "category-selection.json"),
        "sku_snapshot": _load_json(product / "output" / "sku-run-snapshot.json"),
        "copy": _load_json(product / "output" / "copy-ru.json"),
        "category": _load_json(product / "output" / "ozon-category.json"),
        "attributes": _load_json(product / "output" / "ozon-attributes-final.json"),
        "upload_config": _load_json(product / "output" / "ozon-upload-config.json"),
        "draft": _load_json(product / "output" / "ozon-draft.json"),
        "tags": _load_json(product / "output" / "ozon-tags.json"),
        "rich_content": _load_json(product / "output" / "rich-content.json"),
        "image_qc": _load_json(product / "output" / "image-qc-report.json"),
    }


def _stable_value(root: Path, value: Any, key: str = "") -> Any:
    """Remove non-review metadata and normalize local paths for hashing."""
    timestamp_keys = {"created_at", "updated_at", "captured_at", "collected_at", "confirmed_at", "checked_at"}
    if isinstance(value, dict):
        return {
            str(item_key): _stable_value(root, item_value, str(item_key))
            for item_key, item_value in value.items()
            if str(item_key) not in timestamp_keys and not str(item_key).endswith("_at")
        }
    if isinstance(value, list):
        return [_stable_value(root, item, key) for item in value]
    if isinstance(value, str):
        path = Path(value)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                return "<external-absolute-path>"
        if key in {"image", "images", "image_path", "local_image_path", "local_path", "output_path", "path"}:
            return path.as_posix()
    return value


def _find_image_paths(value: Any) -> set[str]:
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"image", "images", "image_path", "local_image_path", "local_path", "path", "output_path"}:
                paths.update(_image_values(item))
            else:
                paths.update(_find_image_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.update(_find_image_paths(item))
    return paths


def _image_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return _find_image_paths(value)
    if isinstance(value, list):
        return {item for value_item in value for item in _image_values(value_item)}
    return set()


def _image_references(artifacts: dict[str, dict[str, Any]]) -> set[str]:
    paths = set()
    for name in ("sku_snapshot", "draft", "image_qc"):
        paths.update(_find_image_paths(artifacts[name]))
    return paths


def _local_image_path(root: Path, reference: str) -> tuple[str, Path] | None:
    path = Path(reference)
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (root / path).resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    return relative, resolved


def _image_manifest(root: Path, artifacts: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    for reference in sorted(_image_references(artifacts)):
        local_image = _local_image_path(root, reference)
        if local_image is None:
            continue
        relative, image_path = local_image
        if image_path.is_file():
            manifest.append({
                "path": relative,
                "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            })
    return manifest


def _missing_image_references(root: Path, artifacts: dict[str, dict[str, Any]]) -> set[str]:
    missing = set()
    for reference in _image_references(artifacts):
        local_image = _local_image_path(root, reference)
        if local_image is None or not local_image[1].is_file():
            missing.add(reference)
    return missing


def _sku_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = snapshot.get("sku_rows") or snapshot.get("skus") or []
    return [item for item in rows if isinstance(item, dict)]


def _final_skus(artifacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge frozen SKU facts with the final, upload-bound offer and price data."""
    drafts = [item for item in artifacts["draft"].get("skus") or [] if isinstance(item, dict)]
    prices = [item for item in artifacts["upload_config"].get("sku_prices") or [] if isinstance(item, dict)]
    draft_by_sku = {
        str(item.get("source_sku_id") or item.get("sku_id") or ""): item
        for item in drafts
    }
    price_by_sku = {
        str(item.get("source_sku_id") or item.get("sku_id") or ""): item
        for item in prices
    }
    result = []
    for snapshot in _sku_rows(artifacts["sku_snapshot"]):
        sku_id = str(snapshot.get("sku_id") or "")
        final = {**snapshot}
        final.update({key: value for key, value in draft_by_sku.get(sku_id, {}).items() if value is not None and value != ""})
        price = price_by_sku.get(sku_id, {})
        if final.get("selling_price_rub") is None and final.get("price") is None:
            final["price"] = price.get("price", price.get("selling_price_rub"))
        result.append(final)
    return result


def _protected_product(root: Path, product_id: str, batch_product: dict[str, Any]) -> dict[str, Any]:
    artifacts = _read_artifacts(root, product_id)
    try:
        from scripts.store_publications import load_publications
        from scripts.task_database import cutover_active, publications_from_db
    except ModuleNotFoundError:
        from store_publications import load_publications
        from task_database import cutover_active, publications_from_db
    product = root / "products" / product_id
    publications = publications_from_db(root, product) if cutover_active(root) else load_publications(product)
    if publications is None:
        raise ValueError("Authoritative publication state is unavailable")
    store_payloads = {
        store_id: {
            "selected": record.get("selected"),
            "title_override": record.get("title_override"),
            "image_version_override": record.get("image_version_override"),
            "skus": [{key: sku.get(key) for key in (
                "sku_id", "offer_id", "initial_price_cny", "initial_price_rub", "price_override_cny",
            )} for sku in record.get("sku_publications") or []],
        }
        for store_id, record in sorted((publications.get("stores") or {}).items())
        if record.get("selected")
    }
    return {
        "product_id": product_id,
        "collection_id": artifacts["source"].get("collection_id"),
        "selected_stores": sorted(batch_product.get("target_store_ids") or artifacts["upload_config"].get("target_store_ids") or []),
        "sku_snapshot": _stable_value(root, artifacts["sku_snapshot"]),
        "final_copy": _stable_value(root, artifacts["copy"]),
        "final_attributes": _stable_value(root, artifacts["attributes"]),
        "upload_config": _stable_value(root, artifacts["upload_config"]),
        "draft": _stable_value(root, artifacts["draft"]),
        "store_payloads": store_payloads,
        "local_edits": {name: _stable_value(root, _load_json(product / name)) for name in (
            "output/workbench-draft.json", "input/workbench-sku-overrides.json",
            "input/category-selection.json", "input/visual-preference.json",
            "input/operator-guidance.json", "output/image-plan.json",
            "output/image-regeneration-request.json", "output/pricing-result.json",
            "output/ozon-tags.json", "output/rich-content.json",
        )},
        "images": _image_manifest(root, artifacts),
    }


def _ordered_batch_products(batch: dict[str, Any]) -> list[dict[str, Any]]:
    products = batch.get("products") or []
    return sorted(
        [item for item in products if isinstance(item, dict) and item.get("product_id")],
        key=lambda item: str(item["product_id"]),
    )


def _protected_document(root: Path, batch: dict[str, Any]) -> dict[str, Any]:
    rows = _ordered_batch_products(batch)
    return {
        "batch_id": str(batch.get("batch_id") or ""),
        "product_ids": [str(item["product_id"]) for item in rows],
        "products": [_protected_product(root, str(item["product_id"]), item) for item in rows],
    }


def compute_batch_review_hash(root: Path, batch: dict) -> str:
    """Return the SHA-256 of the stable, locally protected review payload."""
    return hashlib.sha256(_canonical_json(_protected_document(root, batch)).encode("utf-8")).hexdigest()


def _review_row(root: Path, batch_product: dict[str, Any]) -> dict[str, Any]:
    product_id = str(batch_product["product_id"])
    artifacts = _read_artifacts(root, product_id)
    snapshot_rows = _final_skus(artifacts)
    prices = [
        item.get("selling_price_rub", item.get("price"))
        for item in snapshot_rows
        if isinstance(item.get("selling_price_rub", item.get("price")), (int, float))
    ]
    coverage = _attribute_coverage(artifacts["attributes"])
    return {
        "product_id": product_id,
        "collection_id": artifacts["source"].get("collection_id"),
        "source_title_cn": artifacts["source"].get("title_cn"),
        "title_ru": artifacts["copy"].get("title_ru", artifacts["copy"].get("title")),
        "category_id": artifacts["attributes"].get("category_id", artifacts["upload_config"].get("category_id", artifacts["draft"].get("category_id"))),
        "category_name_zh": artifacts["category_selection"].get("category_name_zh"),
        "category_name_ru": artifacts["category"].get("category_name"),
        "category_path": artifacts["category"].get("category_path") or [],
        "skus": snapshot_rows,
        "sku_count": len(snapshot_rows),
        "price_range": {"min": min(prices) if prices else None, "max": max(prices) if prices else None},
        "image_urls": [item["path"] for item in _image_manifest(root, artifacts)],
        "attribute_coverage": coverage,
        "unknown_count": int(coverage.get("unknown_count") or 0),
        "estimated_count": int(coverage.get("estimated_count") or 0),
        "image_qc_status": artifacts["image_qc"].get("decision"),
        "target_store_ids": sorted(batch_product.get("target_store_ids") or artifacts["upload_config"].get("target_store_ids") or []),
        "risks": [],
    }


def _risk(level: str, code: str, product_id: str, message: str, return_focus: str) -> dict[str, str]:
    return {
        "level": level,
        "code": code,
        "product_id": product_id,
        "message": message,
        "return_focus": return_focus,
    }


def _has_inventory_field(value: Any, allow_draft_placeholders: bool = False, path: tuple[str, ...] = ()) -> bool:
    forbidden = {"inventory", "stock", "warehouse", "warehouse_id", "quantity"}
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized == "stock" and allow_draft_placeholders:
                if path == () and isinstance(nested, dict) and set(nested) == {"quantity", "warehouse_id"} and (
                    nested["quantity"] is None and nested["warehouse_id"] == "unknown"
                ):
                    continue
                if path == ("skus",) and nested is None:
                    continue
            if normalized in forbidden:
                return True
            if _has_inventory_field(nested, allow_draft_placeholders, path + (normalized,)):
                return True
    elif isinstance(value, list):
        return any(_has_inventory_field(item, allow_draft_placeholders, path) for item in value)
    return False


def _unsafe_publication_state(value: Any) -> bool:
    unsafe = {"submitted", "uploading", "processing", "task_id_received", "completed", "published", "success"}
    state_keys = {"publication_state", "upload_status", "store_status", "submission_status"}
    if isinstance(value, dict):
        if value.get("task_id"):
            return True
        for key, nested in value.items():
            normalized_key = str(key).lower()
            if normalized_key in state_keys and str(nested).lower() in unsafe:
                return True
            if normalized_key == "preflight" and isinstance(nested, dict) and str(nested.get("status") or "").lower() in unsafe:
                return True
            if _unsafe_publication_state(nested):
                return True
    elif isinstance(value, list):
        return any(_unsafe_publication_state(item) for item in value)
    return False


def _artifact_path(root: Path, product_id: str, relative: str) -> Path:
    return root / "products" / product_id / relative


def _attribute_coverage(attributes: dict[str, Any]) -> dict[str, Any]:
    summary = attributes.get("required_summary") or {}
    rows = [item for item in attributes.get("attributes") or [] if isinstance(item, dict)]
    return {
        "total": int(summary.get("total") or 0),
        "filled": int(summary.get("filled") or 0),
        "missing": int(summary.get("missing") or 0),
        "missing_attribute_ids": list(summary.get("missing_attribute_ids") or []),
        "unknown_count": sum(item.get("value") == "unknown" for item in rows),
        "estimated_count": sum(item.get("source") == "AI_estimated" for item in rows),
    }


def _placeholder(value: Any) -> bool:
    return bool(re.search(r"(?i)(?:test[- _]*do[- _]*not[- _]*sell|do not sell|placeholder|dummy|\btest\b|测试|占位|示例)", str(value or "")))


def _positive_measurements(config: dict[str, Any]) -> bool:
    for name in ("product_dimensions", "package_dimensions"):
        value = config.get(name) or {}
        if any(not _positive_number(value.get(axis)) for axis in ("length_mm", "width_mm", "height_mm")):
            return False
    for name in ("product_weight", "package_weight"):
        value = (config.get(name) or {}).get("value_g")
        if not _positive_number(value):
            return False
    return True


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _contains_cyrillic(value: Any) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(value or "")))


def _contains_han(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def _content_texts(value: Any, include: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if include and value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _content_texts(item, include)]
    if isinstance(value, dict):
        text_keys = {"text", "text_ru", "title", "title_ru", "description", "description_ru", "content"}
        return [text for key, item in value.items() for text in _content_texts(item, str(key) in text_keys)]
    return []


def _russian_content(value: Any) -> bool:
    texts = _content_texts(value, isinstance(value, str))
    return bool(texts) and all(_contains_cyrillic(text) and not _contains_han(text) for text in texts)


def _category_semantics_match(selection: dict[str, Any], category: dict[str, Any]) -> bool:
    return bool(
        selection.get("selection_source") == "user_final_choice"
        and selection.get("locked_for_batch") is True
        and selection.get("allow_runtime_rematch") is False
        and selection.get("category_id") == category.get("category_id")
        and selection.get("type_id") == category.get("type_id")
        and selection.get("category_name_ru") == category.get("category_name")
        and selection.get("category_path") == category.get("category_path")
        and category.get("metadata_source") == "ozon_seller_api"
    )


def _required_attributes_complete(attributes: dict[str, Any]) -> bool:
    summary = attributes.get("required_summary")
    if not isinstance(summary, dict) or any(key not in summary for key in ("total", "filled", "missing", "missing_attribute_ids")):
        return False
    total, filled, missing = (summary[key] for key in ("total", "filled", "missing"))
    return (
        all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (total, filled, missing))
        and filled + missing == total
        and missing == 0
        and summary["missing_attribute_ids"] == []
    )


def _normalized_image_set(root: Path, references: Any) -> set[str]:
    result = set()
    for reference in _image_values(references):
        local = _local_image_path(root, reference)
        if local:
            result.add(local[0])
    return result


def _product_risks(root: Path, batch_product: dict[str, Any]) -> list[dict[str, str]]:
    product_id = str(batch_product["product_id"])
    artifacts = _read_artifacts(root, product_id)
    risks: list[dict[str, str]] = []

    def blocker(code: str, message: str, focus: str) -> None:
        risks.append(_risk("blocker", code, product_id, message, focus))

    required = {
        "sku_snapshot": ("output/sku-run-snapshot.json", "missing_sku_snapshot", "缺少SKU运行快照", "skus"),
        "copy": ("output/copy-ru.json", "missing_final_copy", "缺少最终俄文文案", "copy"),
        "category": ("output/ozon-category.json", "missing_final_category", "缺少最终Ozon类目确认", "attributes"),
        "attributes": ("output/ozon-attributes-final.json", "missing_final_attributes", "缺少最终Ozon属性", "attributes"),
        "upload_config": ("output/ozon-upload-config.json", "missing_upload_config", "缺少上传配置", "stores"),
        "draft": ("output/ozon-draft.json", "missing_ozon_draft", "缺少最终Ozon草稿", "draft"),
        "tags": ("output/ozon-tags.json", "missing_search_tags", "缺少Ozon搜索标签", "copy"),
        "rich_content": ("output/rich-content.json", "missing_rich_content", "缺少Ozon富内容", "copy"),
        "image_qc": ("output/image-qc-report.json", "missing_image_qc", "缺少图片技术检查报告", "images"),
    }
    for name, (relative, code, message, focus) in required.items():
        if not _artifact_path(root, product_id, relative).is_file() or not artifacts[name]:
            blocker(code, message, focus)

    status = str(artifacts["status"].get("status") or "").upper()
    if status != READY_FOR_BATCH_REVIEW:
        blocker("product_not_ready", "商品尚未达到整批审阅就绪状态", "status")

    source = artifacts["source"]
    expected_collection = batch_product.get("collection_id")
    if source.get("product_id") != product_id or not source.get("collection_id") or (
        expected_collection and source.get("collection_id") != expected_collection
    ):
        blocker("collection_mismatch", "商品归属或采集箱不匹配", "source")

    snapshot = artifacts["sku_snapshot"]
    if snapshot and (
        snapshot.get("product_id") not in {None, product_id}
        or snapshot.get("collection_id") not in {None, source.get("collection_id")}
    ):
        blocker("collection_mismatch", "SKU快照与商品归属不匹配", "skus")
    sku_rows = _final_skus(artifacts)
    if not sku_rows:
        blocker("missing_sku", "未找到可上传SKU", "skus")
    elif len(sku_rows) > 10:
        blocker("too_many_skus", "单个商品最多只能上传10个SKU", "skus")
    else:
        draft_by_sku = {
            str(item.get("source_sku_id") or item.get("sku_id") or ""): item
            for item in artifacts["draft"].get("skus") or []
            if isinstance(item, dict)
        }
        for sku in sku_rows:
            if not sku.get("sku_id"):
                blocker("missing_sku", "SKU标识不完整", "skus")
            elif _placeholder(sku.get("sku_id")):
                blocker("placeholder_listing_identity", "SKU仍是测试或占位内容", "skus")
            final_draft_sku = draft_by_sku.get(str(sku.get("sku_id") or ""), {})
            offer_id = next((final_draft_sku.get(field) for field in ("offer_id", "offer", "offer_code", "offer_identifier") if final_draft_sku.get(field)), None)
            if not offer_id:
                blocker("missing_offer_id", "SKU缺少Ozon offer标识", "skus")
            elif _placeholder(offer_id):
                blocker("placeholder_listing_identity", "Ozon offer标识仍是测试或占位内容", "skus")
            if not _positive_number(sku.get("selling_price_rub", sku.get("price"))):
                blocker("missing_final_price", "SKU缺少最终价格", "pricing")

        final_prices = [
            row.get("price", row.get("selling_price_rub"))
            for row in [*(artifacts["draft"].get("skus") or []), *(artifacts["upload_config"].get("sku_prices") or [])]
            if isinstance(row, dict)
        ]
        if not final_prices or any(not _positive_number(price) for price in final_prices):
            blocker("missing_final_price", "最终上传价格必须是大于零的有限数值", "pricing")

    copy = artifacts["copy"]
    title_ru = str(copy.get("title_ru") or copy.get("title") or "").strip()
    description_ru = str(copy.get("description_ru") or copy.get("description") or "").strip()
    if _placeholder(title_ru):
        blocker("placeholder_listing_identity", "商品标题仍是测试或占位内容", "copy")
    if not _contains_cyrillic(title_ru) or not _contains_cyrillic(description_ru) or _contains_han(title_ru) or _contains_han(description_ru):
        blocker("missing_russian_copy", "俄文标题或详情文案不完整", "copy")
    bullets = copy.get("bullets_ru") or copy.get("selling_points") or copy.get("selling_points_ru")
    if not _russian_content(bullets):
        blocker("missing_russian_copy", "俄文卖点不完整", "copy")

    tags = artifacts["tags"].get("tags") or []
    if not tags or any(not isinstance(tag, str) or not re.fullmatch(r"#[А-Яа-яЁё]+", tag.strip()) for tag in tags):
        blocker("missing_search_tags", "缺少Ozon搜索标签", "copy")
    rich_content = artifacts["rich_content"].get("items") or artifacts["rich_content"].get("content")
    if not rich_content or not _russian_content(rich_content):
        blocker("missing_rich_content", "缺少Ozon富内容", "copy")

    category = artifacts["attributes"].get("category_id", artifacts["upload_config"].get("category_id", artifacts["draft"].get("category_id")))
    if category in {None, "", "unknown"}:
        blocker("missing_final_category", "缺少最终Ozon类目", "attributes")
    category_review = artifacts["category"]
    if category_review.get("category_id") != category or category_review.get("match_status") != "api_confirmed":
        blocker("category_not_confirmed", "Ozon类目尚未通过API确认", "attributes")
    elif not _category_semantics_match(artifacts["category_selection"], category_review):
        blocker("category_selection_mismatch", "用户锁定类目与Ozon API确认资料不一致", "attributes")

    if not _positive_measurements(artifacts["upload_config"]):
        blocker("missing_required_logistics", "商品及包装尺寸重量不完整", "attributes")

    selected_stores = batch_product.get("target_store_ids") or snapshot.get("target_store_ids") or []
    configured_stores = artifacts["upload_config"].get("target_store_ids") or snapshot.get("target_store_ids") or []
    if not selected_stores or not configured_stores:
        blocker("missing_target_store", "缺少目标店铺", "stores")
    elif sorted(selected_stores) != sorted(configured_stores):
        blocker("target_store_mismatch", "批次目标店铺与最终上传配置不一致", "stores")

    qc = artifacts["image_qc"]
    draft_images = _normalized_image_set(root, [
        artifacts["draft"].get("images") or [],
        *[item.get("images") or [] for item in artifacts["draft"].get("skus") or [] if isinstance(item, dict)],
    ])
    checked_images = _normalized_image_set(root, [item.get("path") for item in qc.get("images_checked") or [] if isinstance(item, dict)])
    passed_images = _normalized_image_set(root, [item.get("path") for item in qc.get("technical_checks") or [] if isinstance(item, dict) and str(item.get("status") or "").lower() == "pass"])
    if str(qc.get("decision") or "").lower() not in {"pass", "passed", "ok"} or qc.get("critical_failures"):
        blocker("image_qc_failed", "图片技术检查未通过", "images")
    elif _missing_image_references(root, artifacts):
        blocker("missing_image_file", "最终资料引用的本地图片不存在", "images")
    elif len(draft_images) < 2 or not draft_images.issubset(checked_images) or not draft_images.issubset(passed_images) or any(
        str(item.get("status") or "").lower() != "pass" for item in qc.get("technical_checks") or []
    ) or not qc.get("technical_checks"):
        blocker("image_set_incomplete", "至少两张商品图必须完成技术优化与质检", "images")
    elif not _image_manifest(root, artifacts):
        blocker("missing_image_file", "缺少可上传的本地图片", "images")

    if _has_inventory_field(artifacts["attributes"]) or _has_inventory_field(artifacts["upload_config"]) or _has_inventory_field(artifacts["draft"], allow_draft_placeholders=True):
        blocker("inventory_field_forbidden", "上传请求中禁止包含库存、仓库或数量字段", "draft")
    if _unsafe_publication_state(artifacts["upload_config"]) or _unsafe_publication_state(artifacts["draft"]):
        blocker("unsafe_publication_state", "店铺发布状态不允许安全提交", "stores")

    coverage = _attribute_coverage(artifacts["attributes"])
    if not _required_attributes_complete(artifacts["attributes"]):
        blocker("missing_required_attributes", "Ozon必填属性尚未全部填写", "attributes")
    if coverage["estimated_count"]:
        risks.append(_risk("warning", "low_risk_estimate", product_id, "存在已标记的低风险估算字段", "attributes"))
    if coverage["unknown_count"]:
        risks.append(_risk("warning", "unknown_attribute", product_id, "存在未知属性字段", "attributes"))
    return risks


def build_batch_upload_review(root: Path, batch: dict) -> dict:
    """Build a read-only review snapshot from final local product artifacts."""
    rows = [_review_row(root, item) for item in _ordered_batch_products(batch)]
    risks = [risk for item in _ordered_batch_products(batch) for risk in _product_risks(root, item)]
    risks.sort(key=lambda item: (item["product_id"], item["level"], item["code"]))
    risks_by_product: dict[str, list[dict[str, str]]] = {}
    for risk in risks:
        risks_by_product.setdefault(risk["product_id"], []).append(risk)
    for row in rows:
        row["risks"] = risks_by_product.get(row["product_id"], [])
    stores = sorted({store for row in rows for store in row["target_store_ids"]})
    sku_count = sum(row["sku_count"] for row in rows)
    blockers = [item for item in risks if item["level"] == "blocker"]
    warnings = [item for item in risks if item["level"] == "warning"]
    return {
        "schema_version": "1.0.0",
        "batch_id": str(batch.get("batch_id") or ""),
        "status": BLOCKED_BEFORE_UPLOAD if blockers else AWAITING_BATCH_UPLOAD_CONFIRMATION,
        "can_confirm": not blockers,
        "review_hash": compute_batch_review_hash(root, batch),
        "product_count": len(rows),
        "sku_count": sku_count,
        "store_count": len(stores),
        "expected_submission_count": sum(len(row["target_store_ids"]) for row in rows),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "target_store_ids": stores,
        "products": rows,
        "risks": risks,
        "write_api_calls": 0,
        "inventory_api_calls": 0,
    }


def authorization_path(root: Path, batch_id: str) -> Path:
    """Return the authorization-record location without creating it."""
    return root / "batches" / batch_id / "upload-authorization.json"


def _review_allows_authorized_runtime_replay(root: Path, batch_id: str, review: dict[str, Any], *, allow_upload_retry: bool = False) -> bool:
    if review.get("can_confirm"):
        return True
    blockers = [risk for risk in review.get("risks") or [] if risk.get("level") == "blocker"]
    if not blockers or any(risk.get("code") != "product_not_ready" for risk in blockers):
        return False
    for product_id in {str(risk.get("product_id") or "") for risk in blockers}:
        status = _load_json(root / "products" / product_id / "status.json")
        state = str(status.get("status") or "").upper()
        belongs_to_batch = str(status.get("batch_id") or "") == batch_id
        queued_or_running = (
            belongs_to_batch
            and status.get("task_authorized") is True
            and state in {"QUEUED", "PROCESSING", "UPLOADING"}
            and (status.get("next_action") == "ozon_upload" or status.get("current_step") == "ozon_upload")
        )
        submitted = (
            belongs_to_batch
            and int(status.get("api_write_count") or 0) > 0
            and state in {"HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE"}
        )
        retrying = (
            allow_upload_retry and belongs_to_batch
            and state in {"NEEDS_ATTENTION", "PARTIAL", "WAITING_MANUAL_REVIEW"}
            and (status.get("failed_step") == "ozon_upload" or status.get("current_step") == "ozon_upload")
        )
        if not (queued_or_running or submitted or retrying):
            return False
    return True


def store_upload_is_authorized(root: Path, product_dir: Path, store_id: str) -> bool:
    """Called under batch_upload_lock immediately before the actual store runner."""
    try:
        batch_id = str(_load_json(product_dir / "status.json").get("batch_id") or "")
        if not batch_id or batch_id in {"unknown", ".", ".."} or Path(batch_id).name != batch_id:
            return False
        batch = _load_json(root / "batches" / batch_id / "batch.json")
        record = _load_json(authorization_path(root, batch_id))
        review = build_batch_upload_review(root, batch)
        ordered = _ordered_batch_products(batch)
        entry = next((item for item in ordered if item["product_id"] == product_dir.name), {})
        return (
            record.get("status") == "authorized"
            and record.get("batch_id") == batch_id == batch.get("batch_id")
            and record.get("product_ids") == [str(item["product_id"]) for item in ordered]
            and record.get("target_store_ids") == review.get("target_store_ids")
            and store_id in (entry.get("target_store_ids") or [])
            and hmac.compare_digest(str(record.get("review_hash") or ""), review["review_hash"])
            and _review_allows_authorized_runtime_replay(root, batch_id, review, allow_upload_retry=True)
        )
    except Exception:
        return False


def authorize_batch_upload(root: Path, batch: dict, supplied_hash: str, operator_id: str) -> dict:
    """Persist approval only for the complete, currently eligible review snapshot."""
    with batch_upload_lock(root):
        return _authorize_batch_upload(root, batch, supplied_hash, operator_id)


def _authorize_batch_upload(root: Path, batch: dict, supplied_hash: str, operator_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", supplied_hash):
        raise ValueError("invalid review hash")
    batch_id = str(batch.get("batch_id") or "")
    products = batch.get("products")
    ordered = _ordered_batch_products(batch)
    product_ids = [str(item["product_id"]) for item in ordered]
    if (
        not batch_id
        or not isinstance(products, list)
        or not products
        or len(ordered) != len(products)
        or len(set(product_ids)) != len(product_ids)
    ):
        raise ValueError("batch membership is empty or incomplete")

    review = build_batch_upload_review(root, batch)
    current_hash = str(review.get("review_hash") or "")
    if not hmac.compare_digest(supplied_hash, current_hash):
        raise ValueError("review hash is stale")
    target_store_ids = list(review.get("target_store_ids") or [])
    path = authorization_path(root, batch_id)
    existing = _load_json(path)
    if path.is_file() and existing.get("batch_id") not in {None, "", batch_id}:
        raise ValueError("authorization belongs to another batch")
    if existing.get("status") == "authorized" and hmac.compare_digest(
        str(existing.get("review_hash") or ""), current_hash
    ):
        if existing.get("batch_id") != batch_id or existing.get("product_ids") != product_ids or existing.get("target_store_ids") != target_store_ids:
            raise ValueError("authorization identity or membership does not match")
        if not _review_allows_authorized_runtime_replay(root, batch_id, review):
            raise ValueError("batch review is blocked")
        return existing
    if not review.get("can_confirm"):
        raise ValueError("batch review is blocked")

    authorization = {
        "schema_version": "1.0.0",
        "batch_id": batch_id,
        "review_hash": current_hash,
        "authorized_at": datetime.now(timezone.utc).isoformat(),
        "authorized_by": str(operator_id),
        "product_ids": product_ids,
        "target_store_ids": target_store_ids,
        "status": "authorized",
    }
    try:
        from scripts.pipeline_runtime import write_json_atomic
    except ModuleNotFoundError:
        from pipeline_runtime import write_json_atomic
    write_json_atomic(path, authorization)
    return authorization
