-- J4 local-only model consent and suggestion records; synthetic data only.
CREATE TABLE IF NOT EXISTS model_requests (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, assistant_id TEXT NOT NULL CHECK (assistant_id='jarvis'),
 provider_id TEXT NOT NULL CHECK (provider_id='local_fake'), model_id TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK (purpose IN ('mail_summary','mail_reply_suggestion','knowledge_summary')),
 source_type TEXT NOT NULL CHECK (source_type IN ('email','note')), source_id TEXT NOT NULL,
 payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pending','cancelled','completed','invalidated','expired')),
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_model_requests_owner_status ON model_requests(owner_id,status,created_at);
CREATE TABLE IF NOT EXISTS ai_suggestions (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE REFERENCES model_requests(id),
 kind TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL, content TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected')), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_suggestions_owner_source ON ai_suggestions(owner_id,source_type,source_id);
