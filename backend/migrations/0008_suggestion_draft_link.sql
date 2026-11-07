-- J4b: one local synthetic draft per accepted reply suggestion.
CREATE TABLE IF NOT EXISTS suggestion_draft_links (
 suggestion_id TEXT PRIMARY KEY REFERENCES ai_suggestions(id),
 owner_id TEXT NOT NULL, draft_id TEXT NOT NULL UNIQUE REFERENCES mail_drafts(id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suggestion_draft_owner ON suggestion_draft_links(owner_id,created_at);
