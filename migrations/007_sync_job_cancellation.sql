-- Recover stale work on startup and request cancellation at safe worker boundaries.
ALTER TABLE sync_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_jobs ADD COLUMN cancel_requested_at TEXT;
