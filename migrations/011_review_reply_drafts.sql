CREATE TABLE review_reply_drafts (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    platform TEXT NOT NULL,
    store_id TEXT NOT NULL,
    content TEXT NOT NULL CHECK(length(trim(content)) > 0),
    source TEXT NOT NULL CHECK(source IN ('MANUAL', 'AI_SUGGESTION')),
    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'DISCARDED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(store_id, organization_id, platform) REFERENCES stores(id, organization_id, platform)
);
CREATE INDEX idx_review_reply_drafts_review ON review_reply_drafts(review_id, created_at DESC);
CREATE TRIGGER review_reply_drafts_scope_insert BEFORE INSERT ON review_reply_drafts
WHEN NOT EXISTS (
    SELECT 1 FROM reviews WHERE id=NEW.review_id AND organization_id=NEW.organization_id
      AND platform=NEW.platform AND store_id=NEW.store_id
)
BEGIN SELECT RAISE(ABORT, 'review draft scope mismatch'); END;
CREATE TRIGGER review_reply_drafts_scope_update BEFORE UPDATE ON review_reply_drafts
WHEN NEW.review_id IS NOT OLD.review_id OR NEW.organization_id IS NOT OLD.organization_id
 OR NEW.platform IS NOT OLD.platform OR NEW.store_id IS NOT OLD.store_id
BEGIN SELECT RAISE(ABORT, 'review draft identity cannot change'); END;
