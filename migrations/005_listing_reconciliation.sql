-- Non-destructive listing reconciliation for complete catalog snapshots.
ALTER TABLE marketplace_listings ADD COLUMN last_seen_sync_id TEXT;
ALTER TABLE marketplace_listings ADD COLUMN presence_status TEXT NOT NULL DEFAULT 'UNKNOWN';
CREATE INDEX IF NOT EXISTS idx_listings_store_presence
ON marketplace_listings(store_id, presence_status);
