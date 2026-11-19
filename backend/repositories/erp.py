"""Small repository boundary for ERP V2 business records."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from backend.db.erp import DEFAULT_ORGANIZATION_ID, database


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _listing_row(row) -> dict[str, Any] | None:
    result = _row(row)
    if result is not None:
        raw = result.pop("attributes_json", None)
        result["attributes"] = json.loads(raw) if raw else None
    return result


def _product_center_row(row) -> dict[str, Any]:
    result = dict(row)
    result["platform_skus"] = json.loads(result.pop("platform_skus_json", None) or "[]")
    raw_images = result.pop("remote_images_json", None)
    try:
        images = json.loads(raw_images) if raw_images else []
    except (json.JSONDecodeError, TypeError):
        images = []
    first = images[0] if isinstance(images, list) and images else None
    if isinstance(first, dict):
        first = first.get("url")
    result["image_url"] = first if _public_image_url(first) else None
    return result


def _public_image_url(value: Any) -> bool:
    if not isinstance(value, str) or "\\" in value or any(char.isspace() for char in value):
        return False
    try:
        url = urlsplit(value)
        host = (url.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError):
        return False
    return (
        url.scheme in {"http", "https"} and not url.username and not url.password
        and "." in host and not host.startswith(".") and ":" not in host
        and not host.rsplit(".", 1)[-1].isdigit()
        and not host.endswith((".local", ".localhost", ".localdomain", ".internal"))
    )


def _validate_organization(item: Mapping[str, Any]) -> None:
    if "organization_id" in item and item["organization_id"] is None:
        raise ValueError("organization_id must not be null")


def _store_scope(db, store_id: str):
    row = db.execute("SELECT organization_id, platform FROM stores WHERE id=?", (store_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown store: {store_id}")
    return row["organization_id"], row["platform"]


class ErpRepository:
    def __init__(self, root: Path):
        self.root = Path(root)

    def upsert_store(self, item: Mapping[str, Any]) -> str:
        _validate_organization(item)
        stamp = now()
        with database(self.root) as db:
            db.execute(
                """INSERT INTO stores(
                    id, platform, display_name, seller_id, company_name, currency, enabled,
                    connection_status, credential_ref, last_connected_at, last_sync_at,
                    last_error, created_at, updated_at, organization_id, account_id, external_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    platform=excluded.platform, display_name=excluded.display_name,
                    seller_id=excluded.seller_id, company_name=excluded.company_name,
                    currency=excluded.currency, enabled=excluded.enabled,
                    connection_status=excluded.connection_status,
                    credential_ref=excluded.credential_ref,
                    last_connected_at=excluded.last_connected_at,
                    last_sync_at=COALESCE(excluded.last_sync_at, stores.last_sync_at),
                    last_error=excluded.last_error, updated_at=excluded.updated_at,
                    account_id=COALESCE(excluded.account_id, stores.account_id),
                    external_id=COALESCE(excluded.external_id, stores.external_id)""",
                (
                    item["id"], item.get("platform", "OZON"), item.get("display_name") or item["id"],
                    item.get("seller_id"), item.get("company_name"), item.get("currency", "RUB"),
                    int(bool(item.get("enabled", True))), item.get("connection_status", "NEEDS_VALIDATION"),
                    item.get("credential_ref") or "", item.get("last_connected_at"), item.get("last_sync_at"),
                    item.get("last_error"), item.get("created_at") or stamp, stamp,
                    DEFAULT_ORGANIZATION_ID, item.get("account_id"), item.get("external_id"),
                ),
            )
        return str(item["id"])

    def get_store(self, store_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            result = _row(db.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone())
        if result:
            result["enabled"] = bool(result["enabled"])
        return result

    def list_stores(self) -> list[dict[str, Any]]:
        with database(self.root, readonly=True) as db:
            rows = [_row(row) for row in db.execute("SELECT * FROM stores ORDER BY display_name, id")]
        for row in rows:
            row["enabled"] = bool(row["enabled"])
        return rows

    def upsert_marketplace_account(self, item: Mapping[str, Any]) -> str:
        _validate_organization(item)
        stamp = now()
        with database(self.root) as db:
            db.execute(
                """INSERT INTO marketplace_accounts(id, organization_id, platform, display_name,
                   external_id, credential_ref, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET platform=excluded.platform,
                   display_name=excluded.display_name,
                   external_id=COALESCE(excluded.external_id, marketplace_accounts.external_id),
                   credential_ref=COALESCE(excluded.credential_ref, marketplace_accounts.credential_ref),
                   updated_at=excluded.updated_at""",
                (item["id"], DEFAULT_ORGANIZATION_ID, item["platform"], item.get("display_name") or item["id"],
                 item.get("external_id"), item.get("credential_ref"), item.get("created_at") or stamp, stamp),
            )
        return str(item["id"])

    def get_marketplace_account(self, account_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            return _row(db.execute("SELECT * FROM marketplace_accounts WHERE id=?", (account_id,)).fetchone())

    def list_marketplace_accounts(self) -> list[dict[str, Any]]:
        with database(self.root, readonly=True) as db:
            return [_row(row) for row in db.execute("SELECT * FROM marketplace_accounts ORDER BY display_name, id")]

    def upsert_marketplace_channel(self, item: Mapping[str, Any]) -> str:
        _validate_organization(item)
        stamp = now()
        with database(self.root) as db:
            organization_id, platform = _store_scope(db, item["store_id"])
            if item.get("platform", platform) != platform:
                raise ValueError("Channel platform must match Store")
            db.execute(
                """INSERT INTO marketplace_channels(id, organization_id, platform, store_id,
                   display_name, external_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET store_id=excluded.store_id,
                   display_name=excluded.display_name,
                   external_id=COALESCE(excluded.external_id, marketplace_channels.external_id),
                   updated_at=excluded.updated_at""",
                (item["id"], organization_id, platform, item["store_id"], item.get("display_name") or item["id"],
                 item.get("external_id"), item.get("created_at") or stamp, stamp),
            )
        return str(item["id"])

    def get_marketplace_channel(self, channel_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            return _row(db.execute("SELECT * FROM marketplace_channels WHERE id=?", (channel_id,)).fetchone())

    def list_marketplace_channels(self, store_id: str) -> list[dict[str, Any]]:
        with database(self.root, readonly=True) as db:
            return [_row(row) for row in db.execute(
                "SELECT * FROM marketplace_channels WHERE store_id=? ORDER BY display_name, id", (store_id,),
            )]

    def upsert_sync_cursor(
        self, store_id: str, entity_type: str, cursor: str | None, *, last_success_at: str | None = None,
    ) -> None:
        with database(self.root) as db:
            organization_id, platform = _store_scope(db, store_id)
            db.execute(
                """INSERT INTO sync_cursors(organization_id, platform, store_id, entity_type,
                   cursor, last_success_at, updated_at) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(organization_id, platform, store_id, entity_type) DO UPDATE SET
                   cursor=excluded.cursor, last_success_at=COALESCE(excluded.last_success_at, sync_cursors.last_success_at),
                   updated_at=excluded.updated_at""",
                (organization_id, platform, store_id, entity_type, cursor, last_success_at, now()),
            )

    def get_sync_cursor(self, store_id: str, entity_type: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            organization_id, platform = _store_scope(db, store_id)
            return _row(db.execute(
                "SELECT * FROM sync_cursors WHERE organization_id=? AND platform=? AND store_id=? AND entity_type=?",
                (organization_id, platform, store_id, entity_type),
            ).fetchone())

    def upsert_product(self, item: Mapping[str, Any]) -> str:
        _validate_organization(item)
        stamp = now()
        with database(self.root) as db:
            db.execute(
                """INSERT INTO products(
                    id, source_platform, source_url, source_product_id, title_cn, title_ru,
                    description_ru, brand, category_id, type_id, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    source_platform=excluded.source_platform, source_url=excluded.source_url,
                    source_product_id=excluded.source_product_id, title_cn=excluded.title_cn,
                    title_ru=excluded.title_ru, description_ru=excluded.description_ru,
                    brand=excluded.brand, category_id=excluded.category_id,
                    type_id=excluded.type_id, status=excluded.status, updated_at=excluded.updated_at""",
                (
                    item["id"], item.get("source_platform", "UNKNOWN"), item.get("source_url"),
                    item.get("source_product_id"), item.get("title_cn"), item.get("title_ru"),
                    item.get("description_ru"), item.get("brand"), item.get("category_id"),
                    item.get("type_id"), item.get("status", "DRAFT"), item.get("created_at") or stamp, stamp,
                ),
            )
        return str(item["id"])

    def upsert_sku(self, item: Mapping[str, Any]) -> str:
        _validate_organization(item)
        stamp = now()
        dimensions = item.get("dimensions")
        with database(self.root) as db:
            db.execute(
                """INSERT INTO skus(
                    id, product_id, seller_sku, supplier_sku, barcode, color, size,
                    specification, purchase_price_cny, weight, dimensions_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    product_id=excluded.product_id, seller_sku=excluded.seller_sku,
                    supplier_sku=excluded.supplier_sku, barcode=excluded.barcode,
                    color=excluded.color, size=excluded.size, specification=excluded.specification,
                    purchase_price_cny=excluded.purchase_price_cny, weight=excluded.weight,
                    dimensions_json=excluded.dimensions_json, updated_at=excluded.updated_at""",
                (
                    item["id"], item["product_id"], item.get("seller_sku"), item.get("supplier_sku"),
                    item.get("barcode"), item.get("color"), item.get("size"), item.get("specification"),
                    item.get("purchase_price_cny"), item.get("weight"),
                    json.dumps(dimensions, ensure_ascii=False, sort_keys=True) if dimensions is not None else None,
                    item.get("created_at") or stamp, stamp,
                ),
            )
        return str(item["id"])

    def upsert_listing(self, item: Mapping[str, Any]) -> int:
        _validate_organization(item)
        stamp = now()
        with database(self.root) as db:
            db.execute(
                """INSERT INTO marketplace_listings(
                    store_id, product_id, sku_id, platform, offer_id, ozon_product_id,
                    ozon_sku, listing_status, moderation_status, remote_title, remote_price,
                    remote_stock, visibility, is_fbo_visible, is_fbs_visible, archived,
                    remote_images_json, currency, attributes_status, last_sync_at,
                    last_error, created_at, updated_at, external_id, channel_id, organization_id
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(store_id, platform, offer_id) DO UPDATE SET
                    product_id=excluded.product_id, sku_id=excluded.sku_id,
                    ozon_product_id=excluded.ozon_product_id, ozon_sku=excluded.ozon_sku,
                    listing_status=excluded.listing_status,
                    moderation_status=excluded.moderation_status,
                    remote_title=excluded.remote_title, remote_price=excluded.remote_price,
                    remote_stock=excluded.remote_stock, last_sync_at=excluded.last_sync_at,
                    visibility=excluded.visibility, is_fbo_visible=excluded.is_fbo_visible,
                    is_fbs_visible=excluded.is_fbs_visible, archived=excluded.archived,
                    remote_images_json=excluded.remote_images_json, currency=excluded.currency,
                    attributes_status=excluded.attributes_status,
                    last_error=excluded.last_error, updated_at=excluded.updated_at,
                    external_id=COALESCE(excluded.external_id, marketplace_listings.external_id),
                    channel_id=COALESCE(excluded.channel_id, marketplace_listings.channel_id)""",
                (
                    item["store_id"], item["product_id"], item.get("sku_id"), item.get("platform", "OZON"),
                    item["offer_id"], item.get("ozon_product_id"), item.get("ozon_sku"),
                    item.get("listing_status", "UNKNOWN"), item.get("moderation_status"),
                    item.get("remote_title"), item.get("remote_price"), item.get("remote_stock"),
                    item.get("visibility"), int(bool(item.get("is_fbo_visible"))),
                    int(bool(item.get("is_fbs_visible"))), int(bool(item.get("archived"))),
                    json.dumps(item.get("remote_images") or [], ensure_ascii=False), item.get("currency"),
                    item.get("attributes_status", "PENDING"), item.get("last_sync_at") or stamp,
                    item.get("last_error"), item.get("created_at") or stamp, stamp,
                    item.get("external_id") or (item.get("ozon_product_id") if item.get("platform", "OZON") == "OZON" else None),
                    item.get("channel_id"), _store_scope(db, item["store_id"])[0],
                ),
            )
            row = db.execute(
                "SELECT id FROM marketplace_listings WHERE store_id=? AND platform=? AND offer_id=?",
                (item["store_id"], item.get("platform", "OZON"), item["offer_id"]),
            ).fetchone()
        return int(row["id"])

    def upsert_catalog_page(
        self, store_id: str, items: list[Mapping[str, Any]], *, sync_id: str
    ) -> int:
        rows = []
        for item in items:
            remote_id = str(item.get("product_id") or item.get("id") or "").strip()
            offer_id = str(item.get("offer_id") or "").strip()
            if not remote_id or not offer_id:
                continue
            product_id = f"OZON:{store_id}:{remote_id}"
            rows.append((product_id, remote_id, offer_id, item))
        stamp = now()
        with database(self.root) as db:
            db.executemany(
                """INSERT INTO products(id, source_platform, source_product_id, status, created_at, updated_at)
                   VALUES(?, 'OZON', ?, 'SYNCED', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status='SYNCED', updated_at=excluded.updated_at""",
                [(product_id, remote_id, stamp, stamp) for product_id, remote_id, _, _ in rows],
            )
            db.executemany(
                """INSERT INTO marketplace_listings(
                    store_id, product_id, platform, offer_id, ozon_product_id, listing_status,
                    visibility, is_fbo_visible, is_fbs_visible, archived, attributes_status,
                    last_seen_sync_id, presence_status, last_sync_at, created_at, updated_at, external_id
                ) VALUES(?,?,'OZON',?,?,?,?,?,?,?,?,?,'SEEN',?,?,?,?)
                ON CONFLICT(store_id, platform, offer_id) DO UPDATE SET
                    product_id=excluded.product_id, ozon_product_id=excluded.ozon_product_id,
                    external_id=excluded.external_id,
                    listing_status=excluded.listing_status, visibility=excluded.visibility,
                    is_fbo_visible=excluded.is_fbo_visible, is_fbs_visible=excluded.is_fbs_visible,
                    archived=excluded.archived, last_sync_at=excluded.last_sync_at,
                    last_seen_sync_id=excluded.last_seen_sync_id, presence_status='SEEN',
                    updated_at=excluded.updated_at""",
                [(
                    store_id, product_id, offer_id, remote_id,
                    str(item.get("visibility") or "UNKNOWN"), item.get("visibility"),
                    int(bool(item.get("has_fbo_stocks"))), int(bool(item.get("has_fbs_stocks"))),
                    int(bool(item.get("archived"))), "PENDING", sync_id, stamp, stamp, stamp, remote_id,
                ) for product_id, remote_id, offer_id, item in rows],
            )
        return len(rows)

    def get_listing(self, store_id: str, offer_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            return _listing_row(db.execute(
                "SELECT * FROM marketplace_listings WHERE store_id=? AND offer_id=?",
                (store_id, offer_id),
            ).fetchone())

    def list_store_listings(self, store_id: str) -> list[dict[str, Any]]:
        with database(self.root, readonly=True) as db:
            return [
                _listing_row(row)
                for row in db.execute(
                    "SELECT * FROM marketplace_listings WHERE store_id=? ORDER BY id",
                    (store_id,),
                )
            ]

    def reconcile_store_listings(self, store_id: str, sync_id: str) -> int:
        with database(self.root) as db:
            cursor = db.execute(
                """UPDATE marketplace_listings
                   SET presence_status='NOT_SEEN', updated_at=?
                   WHERE store_id=?
                     AND (last_seen_sync_id IS NULL OR last_seen_sync_id<>?)""",
                (now(), store_id, sync_id),
            )
            return int(cursor.rowcount)

    def persist_listing_attributes(
        self, store_id: str, items: list[Mapping[str, Any]]
    ) -> set[str]:
        matched: set[str] = set()
        stamp = now()
        with database(self.root) as db:
            for item in items:
                offer_id = str(item.get("offer_id") or "").strip()
                if not offer_id:
                    continue
                cursor = db.execute(
                    """UPDATE marketplace_listings
                       SET attributes_json=?, attributes_synced_at=?, attributes_status='SYNCED',
                           last_error=NULL, updated_at=?
                       WHERE store_id=? AND offer_id=?""",
                    (
                        json.dumps(dict(item), ensure_ascii=False, sort_keys=True),
                        stamp,
                        stamp,
                        store_id,
                        offer_id,
                    ),
                )
                if cursor.rowcount:
                    matched.add(offer_id)
        return matched

    def update_listing_details(self, store_id: str, items: list[Mapping[str, Any]]) -> int:
        saved = 0
        with database(self.root) as db:
            for item in items:
                remote_id = str(item.get("id") or item.get("product_id") or "").strip()
                offer_id = str(item.get("offer_id") or "").strip()
                if not remote_id and not offer_id:
                    continue
                row = db.execute(
                    "SELECT id, product_id, ozon_sku FROM marketplace_listings WHERE store_id=? AND (ozon_product_id=? OR offer_id=?) LIMIT 1",
                    (store_id, remote_id, offer_id),
                ).fetchone()
                if row is None:
                    continue
                product_id = str(row["product_id"])
                sku = str(item.get("sku") or "").strip()
                if sku or "sources" in item:
                    db.execute("DELETE FROM listing_platform_skus WHERE listing_id=?", (row["id"],))
                    top_sku = sku or str(row["ozon_sku"] or "")
                    identifiers = ([{"sku": top_sku, "source": "product_info"}] if top_sku else []) + (item.get("sources") or [])
                    for identifier in identifiers:
                        external_sku = str(identifier.get("sku") or "").strip()
                        if external_sku:
                            db.execute(
                                "INSERT OR IGNORE INTO listing_platform_skus VALUES(?,?,?,?,?)",
                                (row["id"], external_sku, str(identifier.get("source") or "unknown"),
                                 str(identifier.get("shipment_type") or ""), now()),
                            )
                sku_id = None
                if sku:
                    sku_id = f"OZON:{store_id}:{sku}"
                    stamp = now()
                    db.execute(
                        """INSERT INTO skus(id, product_id, seller_sku, created_at, updated_at)
                           VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                           product_id=excluded.product_id, seller_sku=excluded.seller_sku,
                           updated_at=excluded.updated_at""",
                        (sku_id, product_id, offer_id, stamp, stamp),
                    )
                db.execute(
                    """UPDATE products SET title_ru=COALESCE(NULLIF(?, ''), title_ru),
                       category_id=COALESCE(?, category_id), type_id=COALESCE(?, type_id),
                       status='SYNCED', updated_at=? WHERE id=?""",
                    (item.get("name") or item.get("title") or "", item.get("description_category_id"),
                     item.get("type_id"), now(), product_id),
                )
                db.execute(
                    """UPDATE marketplace_listings SET sku_id=COALESCE(?, sku_id),
                       ozon_sku=COALESCE(NULLIF(?, ''), ozon_sku),
                       remote_title=COALESCE(NULLIF(?, ''), remote_title), remote_price=?,
                       remote_images_json=?, currency=?, archived=?, last_sync_at=?, updated_at=?
                       WHERE store_id=? AND product_id=?""",
                    (sku_id, sku, item.get("name") or item.get("title") or "", item.get("marketing_price") or item.get("price"),
                     json.dumps(item.get("images") or [], ensure_ascii=False), item.get("currency_code") or item.get("currency"),
                     int(bool(item.get("is_archived") or item.get("archived"))), now(), now(), store_id, product_id),
                )
                saved += 1
        return saved

    def mark_attributes_status(self, store_id: str, offer_ids: list[str], status: str, error: str | None = None) -> None:
        if not offer_ids:
            return
        placeholders = ",".join("?" for _ in offer_ids)
        with database(self.root) as db:
            db.execute(
                f"UPDATE marketplace_listings SET attributes_status=?, last_error=?, updated_at=? WHERE store_id=? AND offer_id IN ({placeholders})",
                [status, error, now(), store_id, *offer_ids],
            )

    def create_sync_job(self, job_type: str, store_id: str) -> str:
        job_id = uuid.uuid4().hex
        with database(self.root) as db:
            db.execute(
                """INSERT INTO sync_jobs(id, type, store_id, status, stage, started_at, message, organization_id, platform)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (job_id, job_type, store_id, "QUEUED", "QUEUED", now(), "等待同步", *_store_scope(db, store_id)),
            )
        return job_id

    def create_or_get_active_sync_job(self, job_type: str, store_id: str, *, entity_id: str | None = None) -> tuple[str, bool]:
        with database(self.root) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM sync_jobs
                   WHERE type=? AND store_id=? AND entity_id IS ? AND status IN ('QUEUED','RUNNING','RETRYING')
                   ORDER BY started_at DESC LIMIT 1""",
                (job_type, store_id, entity_id),
            ).fetchone()
            if row:
                return str(row["id"]), False
            job_id = uuid.uuid4().hex
            db.execute(
                """INSERT INTO sync_jobs(id,type,store_id,entity_id,status,stage,started_at,message,organization_id,platform)
                   VALUES(?,?,?,?,'QUEUED','QUEUED',?,'等待同步',?,?)""",
                (job_id, job_type, store_id, entity_id, now(), *_store_scope(db, store_id)),
            )
            return job_id, True

    def update_sync_job(self, job_id: str, **values: Any) -> None:
        allowed = {"status", "stage", "progress", "total", "success_count", "failure_count", "finished_at", "retry_count", "message", "error"}
        fields = [(key, value) for key, value in values.items() if key in allowed]
        if not fields:
            return
        with database(self.root) as db:
            db.execute(
                f"UPDATE sync_jobs SET {', '.join(f'{key}=?' for key, _ in fields)} WHERE id=?",
                [*(value for _, value in fields), job_id],
            )

    def get_sync_job(self, job_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            return _row(db.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,)).fetchone())

    def interrupt_stale_sync_jobs(self) -> int:
        with database(self.root) as db:
            result = db.execute(
                """UPDATE sync_jobs
                   SET status='INTERRUPTED', stage='INTERRUPTED', finished_at=?,
                       message='Interrupted by service restart'
                   WHERE status IN ('QUEUED','RUNNING','RETRYING')""",
                (now(),),
            )
            return int(result.rowcount)

    def request_sync_job_cancel(self, job_id: str) -> dict[str, Any] | None:
        with database(self.root) as db:
            db.execute(
                """UPDATE sync_jobs SET cancel_requested=1,
                       cancel_requested_at=COALESCE(cancel_requested_at, ?)
                   WHERE id=? AND status IN ('QUEUED','RUNNING','RETRYING')""",
                (now(), job_id),
            )
        return self.get_sync_job(job_id)

    def sync_job_cancel_requested(self, job_id: str) -> bool:
        with database(self.root, readonly=True) as db:
            row = db.execute(
                "SELECT cancel_requested FROM sync_jobs WHERE id=?", (job_id,)
            ).fetchone()
            return bool(row and row["cancel_requested"])

    def latest_sync_job(self, store_id: str, job_type: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            return _row(db.execute(
                "SELECT * FROM sync_jobs WHERE store_id=? AND type=? ORDER BY started_at DESC LIMIT 1",
                (store_id, job_type),
            ).fetchone())

    def add_sync_job_log(self, job_id: str, store_id: str, **item: Any) -> None:
        with database(self.root) as db:
            db.execute(
                """INSERT INTO sync_job_logs(
                    job_id, store_id, stage, endpoint, page, duration_ms, items_received,
                    items_saved, last_id, retry_count, error_type, error_message, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, store_id, item.get("stage") or "UNKNOWN", item.get("endpoint"), item.get("page"),
                 item.get("duration_ms"), item.get("items_received", 0), item.get("items_saved", 0),
                 item.get("last_id"), item.get("retry_count", 0), item.get("error_type"),
                 item.get("error_message"), now()),
            )

    def record_sync_job_item(
        self, job_id: str, entity_id: str, status: str, error: str | None = None
    ) -> None:
        with database(self.root) as db:
            db.execute(
                """INSERT INTO sync_job_items(job_id, entity_id, status, error, created_at)
                   VALUES(?,?,?,?,?)""",
                (job_id, entity_id, status, error, now()),
            )

    def record_sync_job_items(
        self, job_id: str, items: list[tuple[str, str, str | None]]
    ) -> None:
        if not items:
            return
        stamp = now()
        with database(self.root) as db:
            db.executemany(
                """INSERT INTO sync_job_items(job_id, entity_id, status, error, created_at)
                   VALUES(?,?,?,?,?)""",
                [(job_id, entity_id, status, error, stamp) for entity_id, status, error in items],
            )

    def list_sync_job_items(self, job_id: str) -> list[dict[str, Any]]:
        with database(self.root, readonly=True) as db:
            return [
                _row(row)
                for row in db.execute(
                    "SELECT * FROM sync_job_items WHERE job_id=? ORDER BY id", (job_id,)
                )
            ]

    def update_store_last_sync(self, store_id: str, *, error: str | None = None) -> None:
        with database(self.root) as db:
            db.execute(
                "UPDATE stores SET last_sync_at=?, last_error=?, updated_at=? WHERE id=?",
                (now(), error, now(), store_id),
            )

    def add_audit_log(
        self,
        action: str,
        entity_type: str,
        entity_id: str | None,
        details: Mapping[str, Any],
        *,
        actor: str = "system",
        store_id: str | None = None,
    ) -> None:
        with database(self.root) as db:
            if store_id is None and entity_type == "store":
                store_id = entity_id
            organization_id, platform = _store_scope(db, store_id) if store_id is not None else (DEFAULT_ORGANIZATION_ID, None)
            db.execute(
                """INSERT INTO audit_logs(
                    action, entity_type, entity_id, actor, details_json, created_at, organization_id, platform, store_id
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    action,
                    entity_type,
                    entity_id,
                    actor,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    now(),
                    organization_id, platform, store_id,
                ),
            )

    def list_audit_logs(self, entity_id: str) -> list[dict[str, Any]]:
        with database(self.root, readonly=True) as db:
            return [
                _row(row)
                for row in db.execute(
                    "SELECT * FROM audit_logs WHERE entity_id=? ORDER BY id", (entity_id,)
                )
            ]

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            product = _row(db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())
            if product is None:
                return None
            skus = [_row(row) for row in db.execute("SELECT * FROM skus WHERE product_id=? ORDER BY id", (product_id,))]
            listings = [_listing_row(row) for row in db.execute(
                """SELECT l.*, s.display_name AS store_display_name
                   FROM marketplace_listings l JOIN stores s ON s.id=l.store_id
                   WHERE l.product_id=? ORDER BY s.display_name, l.offer_id""", (product_id,),
            )]
        for sku in skus:
            sku["dimensions"] = json.loads(sku.pop("dimensions_json")) if sku.get("dimensions_json") else None
        return {**product, "skus": skus, "listings": listings}

    def list_products(
        self, *, q: str = "", status: str = "", store_id: str = "", category_id: str = "",
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if q.strip():
            clauses.append("(p.id LIKE ? OR p.title_cn LIKE ? OR p.title_ru LIKE ? OR s.seller_sku LIKE ? OR l.offer_id LIKE ?)")
            term = f"%{q.strip()}%"
            params.extend([term] * 5)
        if status.strip():
            clauses.append("p.status=?")
            params.append(status.strip())
        if store_id.strip():
            clauses.append("l.store_id=?")
            params.append(store_id.strip())
        if category_id.strip():
            clauses.append("p.category_id=?")
            params.append(category_id.strip())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 1000))
        base = """FROM products p
            LEFT JOIN skus s ON s.product_id=p.id
            LEFT JOIN marketplace_listings l ON l.product_id=p.id
            LEFT JOIN stores st ON st.id=l.store_id"""
        with database(self.root, readonly=True) as db:
            total = int(db.execute(f"SELECT COUNT(DISTINCT p.id) {base}{where}", params).fetchone()[0])
            rows = [_row(row) for row in db.execute(
                f"""SELECT p.*, COUNT(DISTINCT s.id) AS sku_count,
                    COUNT(DISTINCT l.id) AS listing_count,
                    MIN(s.seller_sku) AS seller_sku,
                    MIN(l.offer_id) AS offer_id,
                    MIN(l.ozon_product_id) AS ozon_product_id,
                    MIN(l.ozon_sku) AS ozon_sku,
                    GROUP_CONCAT(DISTINCT st.display_name) AS stores,
                    MIN(l.remote_title) AS remote_title,
                    MIN(l.remote_price) AS remote_price,
                    SUM(COALESCE(l.remote_stock, 0)) AS remote_stock,
                    MIN(l.remote_images_json) AS remote_images_json,
                    MIN(l.currency) AS currency,
                    MIN(l.visibility) AS visibility,
                    MIN(l.attributes_status) AS attributes_status,
                    MAX(COALESCE(l.updated_at, p.updated_at)) AS last_activity_at
                    {base}{where}
                    GROUP BY p.id ORDER BY last_activity_at DESC, p.id DESC LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            )]
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    def list_product_center(
        self, *, q: str = "", store_id: str = "", listing_status: str = "",
        presence_status: str = "", attribute_status: str = "", page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if q.strip():
            term = f"%{q.strip()}%"
            clauses.append("""(p.id LIKE ? OR p.title_cn LIKE ? OR p.title_ru LIKE ?
                OR k.seller_sku LIKE ? OR l.offer_id LIKE ? OR l.ozon_product_id LIKE ?
                OR l.ozon_sku LIKE ? OR EXISTS (
                    SELECT 1 FROM listing_platform_skus identifiers
                    WHERE identifiers.listing_id=l.id AND identifiers.sku LIKE ?
                )
                OR (l.id IS NULL AND EXISTS (
                    SELECT 1 FROM skus search_sku
                    WHERE search_sku.product_id=p.id AND search_sku.seller_sku LIKE ?
                )))""")
            params.extend([term] * 9)
        for column, value in (
            ("l.store_id", store_id),
            ("l.listing_status", listing_status),
            ("l.presence_status", presence_status),
            ("l.attributes_status", attribute_status),
        ):
            if value.strip():
                clauses.append(f"{column}=?")
                params.append(value.strip())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 200))
        base = """FROM products p
            LEFT JOIN marketplace_listings l ON l.product_id=p.id
            LEFT JOIN skus k ON k.id=CASE WHEN l.id IS NULL THEN
                (SELECT id FROM skus WHERE product_id=p.id ORDER BY id LIMIT 1)
                ELSE l.sku_id END
            LEFT JOIN stores st ON st.id=l.store_id"""
        fields = """l.id AS listing_id, p.id AS product_id, k.id AS sku_id,
            k.seller_sku AS sku, l.offer_id, l.ozon_product_id, l.ozon_sku,
            (SELECT json_group_array(json_object('sku', sku, 'source', source, 'shipment_type', shipment_type))
             FROM listing_platform_skus WHERE listing_id=l.id) AS platform_skus_json,
            COALESCE(NULLIF(l.remote_title, ''), NULLIF(p.title_ru, ''), NULLIF(p.title_cn, ''), p.id) AS title,
            l.store_id, st.display_name AS store, l.remote_images_json,
            l.remote_price AS price, l.currency, l.listing_status,
            l.presence_status, l.attributes_status AS attribute_status,
            l.last_sync_at AS last_sync"""
        with database(self.root, readonly=True) as db:
            total = int(db.execute(f"SELECT COUNT(*) {base}{where}", params).fetchone()[0])
            rows = [
                _product_center_row(row)
                for row in db.execute(
                    f"""SELECT {fields} {base}{where}
                        ORDER BY COALESCE(l.last_sync_at, p.updated_at) DESC, p.id, l.id
                        LIMIT ? OFFSET ?""",
                    [*params, page_size, (page - 1) * page_size],
                )
            ]
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    def get_product_center_detail(self, product_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            product = db.execute(
                """SELECT id AS product_id,
                   COALESCE(NULLIF(title_ru, ''), NULLIF(title_cn, ''), id) AS title
                   FROM products WHERE id=?""",
                (product_id,),
            ).fetchone()
            if product is None:
                return None
            skus = [dict(row) for row in db.execute(
                "SELECT id AS sku_id, seller_sku AS sku FROM skus WHERE product_id=? ORDER BY id",
                (product_id,),
            )]
            listings = [
                _product_center_row(row)
                for row in db.execute(
                    """SELECT l.id AS listing_id, p.id AS product_id, k.id AS sku_id,
                       k.seller_sku AS sku, l.offer_id, l.ozon_product_id, l.ozon_sku,
                       (SELECT json_group_array(json_object('sku', sku, 'source', source, 'shipment_type', shipment_type))
                        FROM listing_platform_skus WHERE listing_id=l.id) AS platform_skus_json,
                       COALESCE(NULLIF(l.remote_title, ''), NULLIF(p.title_ru, ''), NULLIF(p.title_cn, ''), p.id) AS title,
                       l.store_id, st.display_name AS store, l.remote_images_json,
                       l.remote_price AS price, l.currency, l.listing_status,
                       l.presence_status, l.attributes_status AS attribute_status,
                       l.last_sync_at AS last_sync
                       FROM marketplace_listings l
                       JOIN products p ON p.id=l.product_id
                       LEFT JOIN skus k ON k.id=l.sku_id
                       JOIN stores st ON st.id=l.store_id
                       WHERE l.product_id=? ORDER BY st.display_name, l.offer_id""",
                    (product_id,),
                )
            ]
        return {**dict(product), "skus": skus, "listings": listings}
