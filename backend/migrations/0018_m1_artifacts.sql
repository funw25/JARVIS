-- M1 offline vertical loop: fictional artifacts, suggestions, decisions and drafts.
-- Real accounts, real mail bodies and outbound sending are intentionally absent.
CREATE TABLE IF NOT EXISTS m1_artifacts (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('email','post','note')),
 received_at TEXT NOT NULL, thread_len INTEGER NOT NULL DEFAULT 1 CHECK (thread_len >= 1),
 dedupe_key TEXT NOT NULL, duplicate_of TEXT REFERENCES m1_artifacts(id),
 content TEXT NOT NULL, labels_json TEXT NOT NULL, imported_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_m1_artifacts_owner ON m1_artifacts(owner_id, received_at);
CREATE INDEX IF NOT EXISTS idx_m1_artifacts_dedupe ON m1_artifacts(dedupe_key);

CREATE TABLE IF NOT EXISTS m1_suggestions (
 id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES m1_artifacts(id) ON DELETE CASCADE,
 engine TEXT NOT NULL, classification TEXT NOT NULL, priority TEXT NOT NULL,
 wants_todo INTEGER NOT NULL CHECK (wants_todo IN (0,1)),
 wants_draft INTEGER NOT NULL CHECK (wants_draft IN (0,1)),
 confidence REAL NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_m1_suggestions_artifact ON m1_suggestions(artifact_id);

CREATE TABLE IF NOT EXISTS m1_decisions (
 id TEXT PRIMARY KEY, suggestion_id TEXT NOT NULL REFERENCES m1_suggestions(id) ON DELETE CASCADE,
 action TEXT NOT NULL CHECK (action IN ('accept','modify','reject')),
 edits_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS m1_drafts (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES m1_artifacts(id),
 subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_m1_drafts_owner ON m1_drafts(owner_id, created_at);
