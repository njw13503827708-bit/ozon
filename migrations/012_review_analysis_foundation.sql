CREATE TABLE review_analyses (
    id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    platform TEXT NOT NULL,
    store_id TEXT NOT NULL,
    sentiment TEXT,
    topic TEXT,
    issue_type TEXT,
    ai_summary TEXT,
    suggested_reply TEXT,
    confidence REAL CHECK(confidence BETWEEN 0 AND 1),
    analysis_status TEXT NOT NULL CHECK(analysis_status IN ('PENDING', 'COMPLETED', 'FAILED')),
    created_at TEXT NOT NULL,
    FOREIGN KEY(store_id, organization_id, platform) REFERENCES stores(id, organization_id, platform)
);
CREATE INDEX idx_review_analyses_review ON review_analyses(review_id, created_at DESC);
CREATE TRIGGER review_analyses_scope_insert BEFORE INSERT ON review_analyses
WHEN NOT EXISTS (
    SELECT 1 FROM reviews WHERE id=NEW.review_id AND organization_id=NEW.organization_id
      AND platform=NEW.platform AND store_id=NEW.store_id
)
BEGIN SELECT RAISE(ABORT, 'review analysis scope mismatch'); END;
CREATE TRIGGER review_analyses_scope_update BEFORE UPDATE ON review_analyses
WHEN NEW.review_id IS NOT OLD.review_id OR NEW.organization_id IS NOT OLD.organization_id
 OR NEW.platform IS NOT OLD.platform OR NEW.store_id IS NOT OLD.store_id
BEGIN SELECT RAISE(ABORT, 'review analysis identity cannot change'); END;
