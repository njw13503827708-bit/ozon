CREATE TABLE IF NOT EXISTS listing_platform_skus (
    listing_id INTEGER NOT NULL REFERENCES marketplace_listings(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    source TEXT NOT NULL,
    shipment_type TEXT NOT NULL DEFAULT '',
    synced_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, sku, source, shipment_type)
);
CREATE INDEX IF NOT EXISTS idx_platform_sku_search ON listing_platform_skus(sku);
INSERT OR IGNORE INTO listing_platform_skus(listing_id, sku, source, synced_at)
SELECT id, ozon_sku, 'product_info', COALESCE(last_sync_at, updated_at)
FROM marketplace_listings WHERE ozon_sku IS NOT NULL AND ozon_sku != '';
