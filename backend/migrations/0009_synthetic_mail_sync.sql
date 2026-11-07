-- J8: offline synthetic read-only connector and resumable sync state.
CREATE TABLE IF NOT EXISTS synthetic_mail_connections (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, dataset_id TEXT NOT NULL, account_address TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('active','revoked')), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_synthetic_connections_owner ON synthetic_mail_connections(owner_id,status);
CREATE TABLE IF NOT EXISTS synthetic_sync_runs (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, connection_id TEXT NOT NULL REFERENCES synthetic_mail_connections(id),
 status TEXT NOT NULL CHECK (status IN ('running','completed','cancelled')), next_cursor TEXT,
 max_items INTEGER NOT NULL CHECK (max_items>0), page_size INTEGER NOT NULL CHECK (page_size>0),
 processed_count INTEGER NOT NULL DEFAULT 0, imported_count INTEGER NOT NULL DEFAULT 0,
 updated_count INTEGER NOT NULL DEFAULT 0, unchanged_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_synthetic_sync_owner ON synthetic_sync_runs(owner_id,connection_id,status);
CREATE TABLE IF NOT EXISTS synthetic_mail_links (
 connection_id TEXT NOT NULL REFERENCES synthetic_mail_connections(id), external_id TEXT NOT NULL,
 thread_id TEXT NOT NULL, mail_id TEXT NOT NULL REFERENCES mail_items(id),
 PRIMARY KEY(connection_id,external_id), UNIQUE(connection_id,mail_id)
);
