-- Cross-assistant todo sharing is read-only and explicitly granted.
CREATE TABLE IF NOT EXISTS todo_access_grants (
 id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL,
 todo_id TEXT NOT NULL REFERENCES todos(id) ON DELETE CASCADE,
 from_assistant TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 to_assistant TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 granted_at TEXT NOT NULL,
 revoked_at TEXT,
 CHECK (from_assistant <> to_assistant)
);
CREATE INDEX IF NOT EXISTS idx_todo_grants_lookup ON todo_access_grants(owner_id,to_assistant,revoked_at);
