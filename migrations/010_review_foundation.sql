CREATE TABLE reviews (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    platform TEXT NOT NULL,
    store_id TEXT NOT NULL,
    external_id TEXT,
    product_id TEXT REFERENCES products(id),
    listing_id INTEGER REFERENCES marketplace_listings(id),
    mapping_status TEXT NOT NULL CHECK(mapping_status IN ('MAPPED', 'UNMAPPED')),
    text TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    reply_status TEXT NOT NULL CHECK(reply_status IN ('UNKNOWN', 'UNREPLIED', 'REPLIED')),
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(store_id, organization_id, platform) REFERENCES stores(id, organization_id, platform)
);
CREATE UNIQUE INDEX idx_reviews_remote ON reviews(organization_id, platform, store_id, external_id);
CREATE INDEX idx_reviews_filter ON reviews(organization_id, published_at DESC, id);
CREATE INDEX idx_reviews_store_product ON reviews(store_id, product_id);

CREATE TABLE review_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    external_id TEXT,
    media_type TEXT NOT NULL CHECK(media_type IN ('IMAGE', 'VIDEO')),
    source_url TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_review_media_remote ON review_media(review_id, external_id);

CREATE TRIGGER reviews_scope_insert BEFORE INSERT ON reviews
WHEN (NEW.product_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM products WHERE id=NEW.product_id AND organization_id=NEW.organization_id
)) OR (NEW.listing_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM marketplace_listings WHERE id=NEW.listing_id AND organization_id=NEW.organization_id
      AND store_id=NEW.store_id AND platform=NEW.platform AND product_id=NEW.product_id
)) OR (NEW.mapping_status='MAPPED') != (NEW.product_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'reviews scope mismatch'); END;

CREATE TRIGGER reviews_scope_update BEFORE UPDATE ON reviews
WHEN NEW.organization_id IS NOT OLD.organization_id OR NEW.platform IS NOT OLD.platform
 OR NEW.store_id IS NOT OLD.store_id OR NEW.external_id IS NOT OLD.external_id
 OR (NEW.product_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM products WHERE id=NEW.product_id AND organization_id=NEW.organization_id
)) OR (NEW.listing_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM marketplace_listings WHERE id=NEW.listing_id AND organization_id=NEW.organization_id
      AND store_id=NEW.store_id AND platform=NEW.platform AND product_id=NEW.product_id
)) OR (NEW.mapping_status='MAPPED') != (NEW.product_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'reviews scope mismatch'); END;

CREATE TRIGGER listing_retarget_unmaps_reviews BEFORE UPDATE OF product_id ON marketplace_listings
WHEN NEW.product_id IS NOT OLD.product_id
BEGIN
    UPDATE reviews SET product_id=NULL, listing_id=NULL, mapping_status='UNMAPPED',
        updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    WHERE listing_id=OLD.id;
END;
