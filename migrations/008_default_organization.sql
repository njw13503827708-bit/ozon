CREATE TABLE IF NOT EXISTS organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO organizations(id, name, created_at)
VALUES('default', 'Default Organization', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
ALTER TABLE stores ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'default';
