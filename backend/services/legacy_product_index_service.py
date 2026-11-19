"""Read-only indexer for the two current legacy product artifact contracts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.db.erp import DEFAULT_ORGANIZATION_ID, database
from backend.repositories.erp import ErpRepository, now


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError
    return value


def _known(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value or "").strip()
    return None if not text or text.casefold() == "unknown" else text


def _source_contract(product_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = _read_object(product_dir / "input/source.json")
    product_id = _known(source.get("product_id"))
    skus = source.get("skus")
    if (source.get("schema_version") not in (None, "1.0.0")
            or product_id != product_dir.name or not isinstance(skus, list) or not skus
            or any(value is not None and not isinstance(value, str)
                   for value in (source.get("source_url"), source.get("title_cn"), source.get("title_ru")))):
        raise ValueError
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sku in skus:
        if not isinstance(sku, dict):
            raise ValueError
        sku_id = _known(sku.get("sku_id"))
        price = sku.get("purchase_price")
        if (sku_id is None or sku_id in seen
                or (sku.get("sku_name") is not None and not isinstance(sku["sku_name"], str))
                or (price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))))):
            raise ValueError
        seen.add(sku_id)
        normalized.append(sku)
    return source, normalized


def index_legacy_products(products_root: Path, erp_root: Path) -> dict[str, Any]:
    products_root = Path(products_root)
    erp_root = Path(erp_root)
    if erp_root.resolve().is_relative_to(products_root.resolve()):
        raise ValueError("ERP root must be outside the products source tree")
    repository = ErpRepository(erp_root)
    report: dict[str, Any] = {
        "indexed": {"products": 0, "skus": 0, "listings": 0},
        "skipped": [],
    }
    if not products_root.is_dir():
        raise ValueError(f"Products root does not exist: {products_root}")

    for product_dir in sorted(path for path in products_root.iterdir() if path.is_dir()):
        try:
            source, source_skus = _source_contract(product_dir)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            report["skipped"].append({"product_id": product_dir.name, "reason": "invalid source.json"})
            continue

        product_id = product_dir.name
        internal_product_id = f"LEGACY_PRODUCT:{product_id}"
        stamp = now()
        with database(erp_root) as db:
            db.execute("""INSERT INTO products(id,source_platform,source_url,source_product_id,
                       title_cn,title_ru,status,created_at,updated_at,organization_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (
                internal_product_id, "LEGACY", source.get("source_url"), product_id,
                source.get("title_cn"), source.get("title_ru"), "INDEXED", stamp, stamp,
                DEFAULT_ORGANIZATION_ID,
            ))
            existing_product = db.execute(
                "SELECT source_platform,source_product_id,organization_id FROM products WHERE id=?",
                (internal_product_id,),
            ).fetchone()
        if (existing_product["source_platform"] != "LEGACY"
                or existing_product["source_product_id"] != product_id
                or existing_product["organization_id"] != DEFAULT_ORGANIZATION_ID):
            report["skipped"].append({"product_id": product_id, "reason": "existing product conflict"})
            continue
        sku_ids: dict[str, str] = {}
        for source_sku in source_skus:
            source_sku_id = str(source_sku["sku_id"]).strip()
            internal_sku_id = f"LEGACY_SKU:{product_id}:{source_sku_id}"
            with database(erp_root) as db:
                db.execute("""INSERT INTO skus(id,product_id,seller_sku,specification,
                           purchase_price_cny,created_at,updated_at,organization_id)
                           VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (
                    internal_sku_id, internal_product_id, source_sku_id,
                    source_sku.get("sku_name"), source_sku.get("purchase_price"),
                    stamp, stamp, DEFAULT_ORGANIZATION_ID,
                ))
                existing_sku = db.execute(
                    "SELECT product_id,organization_id FROM skus WHERE id=?", (internal_sku_id,)
                ).fetchone()
            if (existing_sku["product_id"] != internal_product_id
                    or existing_sku["organization_id"] != DEFAULT_ORGANIZATION_ID):
                report["skipped"].append({
                    "product_id": product_id, "sku_id": source_sku_id,
                    "reason": "existing sku conflict",
                })
                continue
            sku_ids[source_sku_id] = internal_sku_id
            report["indexed"]["skus"] += 1
        report["indexed"]["products"] += 1

        publication_path = product_dir / "output/store-publications.json"
        if not publication_path.is_file():
            continue
        try:
            publication = _read_object(publication_path)
            stores = publication.get("stores")
            if publication.get("schema_version") not in (None, "1.0.0"):
                raise ValueError
            if publication.get("product_id") not in (None, product_id) or not isinstance(stores, dict):
                raise ValueError
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            report["skipped"].append({"product_id": product_id, "reason": "invalid store-publications.json"})
            continue

        for store_id, record in stores.items():
            store_id = str(store_id).strip()
            if not isinstance(record, dict) or record.get("selected") is not True:
                continue
            store = repository.get_store(store_id)
            if store is None:
                report["skipped"].append({
                    "product_id": product_id, "store_id": store_id, "reason": "unknown store",
                })
                continue
            if store["organization_id"] != DEFAULT_ORGANIZATION_ID:
                report["skipped"].append({
                    "product_id": product_id, "store_id": store_id,
                    "reason": "store organization mismatch",
                })
                continue
            if store["platform"] != "OZON" or not isinstance(record.get("sku_publications"), list):
                report["skipped"].append({
                    "product_id": product_id, "store_id": store_id, "reason": "unsupported store publication",
                })
                continue
            for item in record["sku_publications"]:
                if not isinstance(item, dict):
                    report["skipped"].append({
                        "product_id": product_id, "store_id": store_id, "reason": "invalid sku publication",
                    })
                    continue
                source_sku_id = _known(item.get("sku_id"))
                if source_sku_id not in sku_ids:
                    report["skipped"].append({
                        "product_id": product_id, "store_id": store_id,
                        "sku_id": source_sku_id, "reason": "unknown source sku",
                    })
                    continue
                offer_id = _known(item.get("offer_id"))
                if offer_id is None:
                    report["skipped"].append({
                        "product_id": product_id, "store_id": store_id,
                        "sku_id": source_sku_id, "reason": "missing offer_id",
                    })
                    continue
                price = item.get("initial_price_rub")
                if (any(value is not None and not isinstance(value, str)
                        for value in (record.get("status"), item.get("moderation_status")))
                        or (price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))))):
                    report["skipped"].append({
                        "product_id": product_id, "store_id": store_id,
                        "sku_id": source_sku_id, "reason": "invalid sku publication",
                    })
                    continue
                ozon_product_id = _known(item.get("ozon_product_id"))
                with database(erp_root) as db:
                    db.execute("""INSERT INTO marketplace_listings(
                               store_id,product_id,sku_id,platform,offer_id,ozon_product_id,
                               listing_status,moderation_status,remote_title,remote_price,currency,
                               attributes_status,created_at,updated_at,external_id,organization_id)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT DO NOTHING""", (
                        store_id, internal_product_id, sku_ids[source_sku_id], "OZON", offer_id,
                        ozon_product_id, str(record.get("status") or "UNKNOWN"),
                        item.get("moderation_status"), source.get("title_ru") or source.get("title_cn"),
                        price, "RUB", "PENDING", stamp, stamp, ozon_product_id,
                        DEFAULT_ORGANIZATION_ID,
                    ))
                    listing = db.execute("""SELECT product_id,sku_id FROM marketplace_listings
                                         WHERE store_id=? AND platform='OZON' AND offer_id=?""",
                                         (store_id, offer_id)).fetchone()
                if listing is None:
                    report["skipped"].append({
                        "product_id": product_id, "store_id": store_id,
                        "sku_id": source_sku_id, "reason": "external identity conflict",
                    })
                elif (listing["product_id"] == internal_product_id
                        and listing["sku_id"] == sku_ids[source_sku_id]):
                    report["indexed"]["listings"] += 1
                else:
                    report["skipped"].append({
                        "product_id": product_id, "store_id": store_id,
                        "sku_id": source_sku_id, "reason": "existing listing conflict",
                    })
    return report
