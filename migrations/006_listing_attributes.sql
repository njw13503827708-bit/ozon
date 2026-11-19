-- Minimal V1.1 persistence for complete Ozon attribute response items.
ALTER TABLE marketplace_listings ADD COLUMN attributes_json TEXT;
ALTER TABLE marketplace_listings ADD COLUMN attributes_synced_at TEXT;
