-- J2b: synthetic local capture inbox and knowledge notes.
CREATE TABLE IF NOT EXISTS capture_items (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
 source_kind TEXT NOT NULL CHECK (source_kind='manual_synthetic'),
 title TEXT NOT NULL, content TEXT NOT NULL, source_url TEXT,
 status TEXT NOT NULL DEFAULT 'inbox' CHECK (status IN ('inbox','processed','archived')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_capture_owner_status ON capture_items(owner_id,status,updated_at);
CREATE TABLE IF NOT EXISTS knowledge_items (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 source_capture_id TEXT REFERENCES capture_items(id),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_owner_updated ON knowledge_items(owner_id,updated_at);
