CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    platform TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    source TEXT NOT NULL,
    currency TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    metrics_status_json TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    completeness TEXT NOT NULL,
    job_id TEXT REFERENCES sync_jobs(id)
);
CREATE INDEX IF NOT EXISTS idx_analytics_store_dates ON analytics_snapshots(store_id, date_from, date_to, fetched_at);
