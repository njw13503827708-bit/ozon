-- Additive migration: stable canonical/listing IDs and existing Ozon fields are retained.
CREATE TABLE marketplace_accounts (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL DEFAULT 'default' REFERENCES organizations(id),
    platform TEXT NOT NULL,
    display_name TEXT NOT NULL,
    external_id TEXT,
    credential_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, organization_id, platform),
    UNIQUE(organization_id, platform, external_id)
);
ALTER TABLE stores ADD COLUMN account_id TEXT REFERENCES marketplace_accounts(id);
ALTER TABLE stores ADD COLUMN external_id TEXT;
CREATE UNIQUE INDEX idx_stores_scope ON stores(id, organization_id, platform);

CREATE TABLE marketplace_channels (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL DEFAULT 'default' REFERENCES organizations(id),
    platform TEXT NOT NULL,
    store_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    external_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(store_id, organization_id, platform) REFERENCES stores(id, organization_id, platform),
    UNIQUE(id, organization_id, platform, store_id),
    UNIQUE(platform, store_id, external_id)
);
ALTER TABLE products ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE skus ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE marketplace_listings ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE marketplace_listings ADD COLUMN ozon_product_id TEXT;
ALTER TABLE marketplace_listings ADD COLUMN sku_id TEXT REFERENCES skus(id) ON DELETE SET NULL;
ALTER TABLE marketplace_listings ADD COLUMN channel_id TEXT REFERENCES marketplace_channels(id);
ALTER TABLE marketplace_listings ADD COLUMN external_id TEXT;
UPDATE marketplace_listings SET external_id=NULLIF(ozon_product_id, '') WHERE platform='OZON';
CREATE UNIQUE INDEX idx_listings_external_identity ON marketplace_listings(platform, store_id, external_id);

ALTER TABLE sync_jobs ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE sync_jobs ADD COLUMN platform TEXT;
UPDATE sync_jobs SET platform=(SELECT platform FROM stores WHERE id=sync_jobs.store_id);
ALTER TABLE audit_logs ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'default';
ALTER TABLE audit_logs ADD COLUMN platform TEXT;
ALTER TABLE audit_logs ADD COLUMN store_id TEXT REFERENCES stores(id);
UPDATE audit_logs SET store_id=entity_id, platform=(SELECT platform FROM stores WHERE id=entity_id)
WHERE entity_type='store' AND entity_id IN (SELECT id FROM stores);

CREATE TABLE sync_cursors (
    organization_id TEXT NOT NULL DEFAULT 'default' REFERENCES organizations(id),
    platform TEXT NOT NULL,
    store_id TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK(length(trim(entity_type)) > 0),
    cursor TEXT,
    last_success_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(organization_id, platform, store_id, entity_type),
    FOREIGN KEY(store_id, organization_id, platform) REFERENCES stores(id, organization_id, platform)
);
-- SQLite cannot add composite foreign keys to populated tables without rebuilding.
-- Triggers enforce the same scope relationships for the retained legacy tables.

CREATE TRIGGER stores_scope_insert BEFORE INSERT ON stores
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
 OR (NEW.account_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM marketplace_accounts WHERE id=NEW.account_id AND organization_id=NEW.organization_id AND platform=NEW.platform))
BEGIN SELECT RAISE(ABORT, 'stores scope mismatch'); END;

CREATE TRIGGER stores_scope_update BEFORE UPDATE ON stores
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
 OR (NEW.account_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM marketplace_accounts WHERE id=NEW.account_id AND organization_id=NEW.organization_id AND platform=NEW.platform))
BEGIN SELECT RAISE(ABORT, 'stores scope mismatch'); END;

CREATE TRIGGER products_scope_insert BEFORE INSERT ON products
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
BEGIN SELECT RAISE(ABORT, 'products scope mismatch'); END;

CREATE TRIGGER products_scope_update BEFORE UPDATE ON products
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
BEGIN SELECT RAISE(ABORT, 'products scope mismatch'); END;

CREATE TRIGGER skus_scope_insert BEFORE INSERT ON skus
WHEN NOT EXISTS(SELECT 1 FROM products WHERE id=NEW.product_id AND organization_id=NEW.organization_id)
BEGIN SELECT RAISE(ABORT, 'skus scope mismatch'); END;

CREATE TRIGGER skus_scope_update BEFORE UPDATE ON skus
WHEN NOT EXISTS(SELECT 1 FROM products WHERE id=NEW.product_id AND organization_id=NEW.organization_id)
BEGIN SELECT RAISE(ABORT, 'skus scope mismatch'); END;

CREATE TRIGGER marketplace_listings_scope_insert BEFORE INSERT ON marketplace_listings
WHEN NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id AND organization_id=NEW.organization_id AND platform=NEW.platform)
 OR NOT EXISTS(SELECT 1 FROM products WHERE id=NEW.product_id AND organization_id=NEW.organization_id)
 OR (NEW.sku_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM skus WHERE id=NEW.sku_id AND product_id=NEW.product_id AND organization_id=NEW.organization_id))
 OR (NEW.channel_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM marketplace_channels WHERE id=NEW.channel_id AND store_id=NEW.store_id AND platform=NEW.platform AND organization_id=NEW.organization_id))
BEGIN SELECT RAISE(ABORT, 'marketplace_listings scope mismatch'); END;

CREATE TRIGGER marketplace_listings_scope_update BEFORE UPDATE ON marketplace_listings
WHEN NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id AND organization_id=NEW.organization_id AND platform=NEW.platform)
 OR NOT EXISTS(SELECT 1 FROM products WHERE id=NEW.product_id AND organization_id=NEW.organization_id)
 OR (NEW.sku_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM skus WHERE id=NEW.sku_id AND product_id=NEW.product_id AND organization_id=NEW.organization_id))
 OR (NEW.channel_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM marketplace_channels WHERE id=NEW.channel_id AND store_id=NEW.store_id AND platform=NEW.platform AND organization_id=NEW.organization_id))
BEGIN SELECT RAISE(ABORT, 'marketplace_listings scope mismatch'); END;

CREATE TRIGGER sync_jobs_scope_insert BEFORE INSERT ON sync_jobs
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
 OR (NEW.store_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id AND organization_id=NEW.organization_id AND platform=NEW.platform))
BEGIN SELECT RAISE(ABORT, 'sync_jobs scope mismatch'); END;

CREATE TRIGGER sync_jobs_scope_update BEFORE UPDATE ON sync_jobs
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
 OR (NEW.store_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id AND organization_id=NEW.organization_id AND platform=NEW.platform))
BEGIN SELECT RAISE(ABORT, 'sync_jobs scope mismatch'); END;

CREATE TRIGGER audit_logs_scope_insert BEFORE INSERT ON audit_logs
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
 OR (NEW.store_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id AND organization_id=NEW.organization_id AND platform=NEW.platform))
BEGIN SELECT RAISE(ABORT, 'audit_logs scope mismatch'); END;

CREATE TRIGGER audit_logs_scope_update BEFORE UPDATE ON audit_logs
WHEN NOT EXISTS(SELECT 1 FROM organizations WHERE id=NEW.organization_id)
 OR (NEW.store_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM stores WHERE id=NEW.store_id AND organization_id=NEW.organization_id AND platform=NEW.platform))
BEGIN SELECT RAISE(ABORT, 'audit_logs scope mismatch'); END;

CREATE TRIGGER stores_scope_immutable BEFORE UPDATE ON stores
WHEN NEW.organization_id IS NOT OLD.organization_id OR NEW.platform IS NOT OLD.platform
BEGIN SELECT RAISE(ABORT, 'stores scope is immutable'); END;

CREATE TRIGGER products_scope_immutable BEFORE UPDATE ON products
WHEN NEW.organization_id IS NOT OLD.organization_id
BEGIN SELECT RAISE(ABORT, 'products scope is immutable'); END;

CREATE TRIGGER skus_scope_immutable BEFORE UPDATE ON skus
WHEN NEW.organization_id IS NOT OLD.organization_id
BEGIN SELECT RAISE(ABORT, 'skus scope is immutable'); END;

CREATE TRIGGER marketplace_accounts_scope_immutable BEFORE UPDATE ON marketplace_accounts
WHEN NEW.organization_id IS NOT OLD.organization_id OR NEW.platform IS NOT OLD.platform
BEGIN SELECT RAISE(ABORT, 'marketplace_accounts scope is immutable'); END;

CREATE TRIGGER marketplace_channels_scope_immutable BEFORE UPDATE ON marketplace_channels
WHEN NEW.organization_id IS NOT OLD.organization_id OR NEW.platform IS NOT OLD.platform OR NEW.store_id IS NOT OLD.store_id
BEGIN SELECT RAISE(ABORT, 'marketplace_channels scope is immutable'); END;

CREATE TRIGGER skus_listing_product_update BEFORE UPDATE OF product_id ON skus
WHEN EXISTS(SELECT 1 FROM marketplace_listings WHERE sku_id=OLD.id AND product_id<>NEW.product_id)
BEGIN SELECT RAISE(ABORT, 'SKU is linked to a different listing product'); END;

CREATE TRIGGER organizations_legacy_delete BEFORE DELETE ON organizations
WHEN EXISTS(SELECT 1 FROM stores WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM products WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM skus WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM marketplace_listings WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM sync_jobs WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM audit_logs WHERE organization_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'Organization still owns ERP records'); END;

CREATE TRIGGER organizations_legacy_update BEFORE UPDATE OF id ON organizations
WHEN EXISTS(SELECT 1 FROM stores WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM products WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM skus WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM marketplace_listings WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM sync_jobs WHERE organization_id=OLD.id)
 OR EXISTS(SELECT 1 FROM audit_logs WHERE organization_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'Organization still owns ERP records'); END;
