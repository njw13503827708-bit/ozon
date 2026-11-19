-- ERP V2 Phase 0/1. Executed idempotently by backend/db/erp.py.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- The executable schema contains these entities and their indexes:
-- stores, products, skus, marketplace_listings, sync_jobs,
-- sync_job_items, audit_logs, schema_migrations.
-- Secrets are intentionally absent; stores.credential_ref points to the
-- owner-only local secret managed by scripts/workbench_stores.py.
