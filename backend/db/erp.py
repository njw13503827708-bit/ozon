"""SQLite connection and idempotent Phase 0/1 ERP migration."""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path


SCHEMA_VERSION = "014"
DEFAULT_ORGANIZATION_ID = "default"
DB_RELATIVE_PATH = Path("runtime/erp.sqlite3")


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stores (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    display_name TEXT NOT NULL,
    seller_id TEXT,
    company_name TEXT,
    currency TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    connection_status TEXT NOT NULL,
    credential_ref TEXT NOT NULL,
    last_connected_at TEXT,
    last_sync_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    source_platform TEXT NOT NULL,
    source_url TEXT,
    source_product_id TEXT,
    title_cn TEXT,
    title_ru TEXT,
    description_ru TEXT,
    brand TEXT,
    category_id TEXT,
    type_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skus (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    seller_sku TEXT,
    supplier_sku TEXT,
    barcode TEXT,
    color TEXT,
    size TEXT,
    specification TEXT,
    purchase_price_cny REAL,
    weight REAL,
    dimensions_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id TEXT NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku_id TEXT REFERENCES skus(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    ozon_product_id TEXT,
    ozon_sku TEXT,
    listing_status TEXT NOT NULL,
    moderation_status TEXT,
    remote_title TEXT,
    remote_price REAL,
    remote_stock INTEGER,
    visibility TEXT,
    is_fbo_visible INTEGER NOT NULL DEFAULT 0,
    is_fbs_visible INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    remote_images_json TEXT,
    currency TEXT,
    attributes_status TEXT NOT NULL DEFAULT 'PENDING',
    attributes_json TEXT,
    attributes_synced_at TEXT,
    last_seen_sync_id TEXT,
    presence_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_sync_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(store_id, platform, offer_id)
);
CREATE TABLE IF NOT EXISTS sync_jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    store_id TEXT REFERENCES stores(id) ON DELETE SET NULL,
    entity_id TEXT,
    status TEXT NOT NULL,
    stage TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES sync_jobs(id) ON DELETE CASCADE,
    entity_id TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES sync_jobs(id) ON DELETE CASCADE,
    store_id TEXT,
    stage TEXT NOT NULL,
    endpoint TEXT,
    page INTEGER,
    duration_ms INTEGER,
    items_received INTEGER NOT NULL DEFAULT 0,
    items_saved INTEGER NOT NULL DEFAULT 0,
    last_id TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
CREATE INDEX IF NOT EXISTS idx_skus_product ON skus(product_id);
CREATE INDEX IF NOT EXISTS idx_listings_product ON marketplace_listings(product_id);
CREATE INDEX IF NOT EXISTS idx_listings_store_status ON marketplace_listings(store_id, listing_status);
CREATE INDEX IF NOT EXISTS idx_sync_job_logs_job ON sync_job_logs(job_id, id);
"""


MIGRATION_COLUMNS = {
    "marketplace_listings": {
        "ozon_sku": "TEXT",
        "last_sync_at": "TEXT",
        "updated_at": "TEXT",
        "visibility": "TEXT",
        "is_fbo_visible": "INTEGER NOT NULL DEFAULT 0",
        "is_fbs_visible": "INTEGER NOT NULL DEFAULT 0",
        "archived": "INTEGER NOT NULL DEFAULT 0",
        "remote_images_json": "TEXT",
        "currency": "TEXT",
        "attributes_status": "TEXT NOT NULL DEFAULT 'PENDING'",
        "attributes_json": "TEXT",
        "attributes_synced_at": "TEXT",
        "last_seen_sync_id": "TEXT",
        "presence_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    },
    "sync_jobs": {
        "stage": "TEXT",
        "message": "TEXT",
        "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
        "cancel_requested_at": "TEXT",
    },
}


def database_path(root: Path) -> Path:
    return Path(root) / DB_RELATIVE_PATH


def connect(root: Path, *, readonly: bool = False, create: bool = False) -> sqlite3.Connection:
    path = database_path(root)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        raise RuntimeError("ERP database missing; run the explicit database initialization command")
    mode = "ro" if readonly else ("rwc" if create else "rw")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=" + mode, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    if create:
        db.execute("PRAGMA journal_mode=WAL")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    else:
        try:
            require_current(db)
        except Exception:
            db.close()
            raise
    return db


def require_current(db: sqlite3.Connection) -> None:
    try:
        versions = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    except sqlite3.Error as exc:
        raise RuntimeError("ERP schema missing; run the explicit database initialization command") from exc
    if not {f"{version:03}" for version in range(8, 15)} <= versions:
        raise RuntimeError("ERP schema is outdated; run the explicit database migration command")


def initialize(root: Path) -> Path:
    from datetime import datetime, timezone

    path = database_path(root)
    with closing(connect(root, create=True)) as db:
        db.executescript(SCHEMA)
        for table, columns in MIGRATION_COLUMNS.items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        store_columns = {row[1] for row in db.execute("PRAGMA table_info(stores)")}
        if "organization_id" not in store_columns:
            migration = Path(__file__).resolve().parents[2] / "migrations/008_default_organization.sql"
            try:
                db.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            except sqlite3.Error:
                db.rollback()
                raise
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES ('008', ?)",
            (datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),),
        )
        db.commit()
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version='009'").fetchone():
            migration = Path(__file__).resolve().parents[2] / "migrations/009_unified_marketplace_foundation.sql"
            try:
                db.execute("BEGIN IMMEDIATE")
                if not db.execute("SELECT 1 FROM schema_migrations WHERE version='009'").fetchone():
                    statement = ""
                    for line in migration.read_text(encoding="utf-8").splitlines():
                        if line.lstrip().startswith("--"):
                            continue
                        statement += line + "\n"
                        if not sqlite3.complete_statement(statement):
                            continue
                        words = statement.split()
                        # Older minimal listing schemas also need the retained Ozon columns.
                        if words[:2] == ["ALTER", "TABLE"]:
                            columns = {row[1] for row in db.execute(f"PRAGMA table_info({words[2]})")}
                            if words[5] in columns:
                                statement = ""
                                continue
                        db.execute(statement)
                        statement = ""
                    db.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES ('009', ?)",
                        (datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),),
                    )
                db.commit()
            except sqlite3.Error:
                db.rollback()
                raise
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version='010'").fetchone():
            migration = Path(__file__).resolve().parents[2] / "migrations/010_review_foundation.sql"
            try:
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") +
                    "\nINSERT INTO schema_migrations(version, applied_at) VALUES "
                    "('010', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\nCOMMIT;"
                )
            except sqlite3.Error:
                db.rollback()
                raise
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version='011'").fetchone():
            migration = Path(__file__).resolve().parents[2] / "migrations/011_review_reply_drafts.sql"
            try:
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") +
                    "\nINSERT INTO schema_migrations(version, applied_at) VALUES "
                    "('011', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\nCOMMIT;"
                )
            except sqlite3.Error:
                db.rollback()
                raise
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version='012'").fetchone():
            migration = Path(__file__).resolve().parents[2] / "migrations/012_review_analysis_foundation.sql"
            try:
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") +
                    "\nINSERT INTO schema_migrations(version, applied_at) VALUES "
                    "('012', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\nCOMMIT;"
                )
            except sqlite3.Error:
                db.rollback()
                raise
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version='013'").fetchone():
            migration = Path(__file__).resolve().parents[2] / "migrations/013_listing_platform_skus.sql"
            try:
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") +
                    "\nINSERT INTO schema_migrations(version, applied_at) VALUES "
                    "('013', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\nCOMMIT;"
                )
            except sqlite3.Error:
                db.rollback()
                raise
        if not db.execute("SELECT 1 FROM schema_migrations WHERE version='014'").fetchone():
            migration = Path(__file__).resolve().parents[2] / "migrations/014_analytics_snapshots.sql"
            try:
                db.executescript(
                    "BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") +
                    "\nINSERT INTO schema_migrations(version, applied_at) VALUES "
                    "('014', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));\nCOMMIT;"
                )
            except sqlite3.Error:
                db.rollback()
                raise
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_store_presence "
            "ON marketplace_listings(store_id, presence_status)"
        )
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")),
        )
        db.commit()
    return path


@contextmanager
def database(root: Path, *, readonly: bool = False):
    from scripts.batch_upload_approval import batch_upload_lock
    with nullcontext() if readonly else batch_upload_lock(Path(root),wait_seconds=30):
        db = connect(root, readonly=readonly)
        try:
            yield db
            if not readonly:
                db.commit()
        finally:
            db.close()
