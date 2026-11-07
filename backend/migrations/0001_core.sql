-- Jarvis local core schema, version 1
-- Synthetic/local development data only. No at-rest encryption is implemented here.
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assistant_profiles (
 assistant_id TEXT PRIMARY KEY CHECK (assistant_id IN ('companion','jarvis')),
 display_name TEXT NOT NULL, role TEXT NOT NULL, persona_version INTEGER NOT NULL DEFAULT 1 CHECK (persona_version > 0),
 enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, assistant_id TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','deleted')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_conversations_owner_assistant ON conversations(owner_id, assistant_id, updated_at);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK (role IN ('user','assistant','system')), body TEXT NOT NULL,
 source_refs_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_time ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS memory_namespaces (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK (kind IN ('profile_shared','companion','work','conversation_raw')),
 created_at TEXT NOT NULL, UNIQUE(owner_id, kind));
CREATE TABLE IF NOT EXISTS memory_items (
 id TEXT PRIMARY KEY, namespace_id TEXT NOT NULL REFERENCES memory_namespaces(id),
 created_by_assistant TEXT NOT NULL REFERENCES assistant_profiles(assistant_id), category TEXT NOT NULL, content TEXT NOT NULL,
 confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
 status TEXT NOT NULL CHECK (status IN ('candidate','approved','forgotten','deleted')),
 approved_at TEXT, expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_memory_namespace_status ON memory_items(namespace_id, status, updated_at);
CREATE TABLE IF NOT EXISTS memory_sources (
 memory_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL CHECK (source_type IN ('message','capture','user_entry','import')),
 source_id TEXT NOT NULL, excerpt TEXT, created_at TEXT NOT NULL, PRIMARY KEY(memory_id, source_type, source_id));
CREATE TABLE IF NOT EXISTS memory_access_grants (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
 from_assistant TEXT NOT NULL REFERENCES assistant_profiles(assistant_id), to_assistant TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 namespace_id TEXT REFERENCES memory_namespaces(id), memory_id TEXT REFERENCES memory_items(id), granted_at TEXT NOT NULL, revoked_at TEXT,
 CHECK (from_assistant <> to_assistant), CHECK (namespace_id IS NOT NULL OR memory_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS idx_memory_grants_lookup ON memory_access_grants(owner_id, to_assistant, revoked_at);
CREATE TABLE IF NOT EXISTS continuity_snapshots (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, assistant_id TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 conversation_id TEXT NOT NULL REFERENCES conversations(id), summary TEXT NOT NULL, topics_json TEXT NOT NULL DEFAULT '[]',
 open_items_json TEXT NOT NULL DEFAULT '[]', source_refs_json TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL CHECK (status IN ('draft','accepted','discarded','revoked')), created_at TEXT NOT NULL, accepted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_snapshots_owner_assistant ON continuity_snapshots(owner_id, assistant_id, status, created_at);
CREATE TABLE IF NOT EXISTS todos (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created_by_assistant TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','done','cancelled')),
 priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
 due_at TEXT, completed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_todos_owner_status_due ON todos(owner_id, status, due_at);
CREATE TABLE IF NOT EXISTS todo_sources (
 todo_id TEXT NOT NULL REFERENCES todos(id) ON DELETE CASCADE, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
 relation TEXT NOT NULL DEFAULT 'derived_from', PRIMARY KEY(todo_id, source_type, source_id));
CREATE TABLE IF NOT EXISTS action_proposals (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL, target_ref TEXT,
 payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','expired','executed','failed','invalidated')),
 expires_at TEXT NOT NULL, approved_at TEXT, executed_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, actor_type TEXT NOT NULL CHECK (actor_type IN ('user','assistant','system')),
 actor_id TEXT NOT NULL, event_type TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
 metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_audit_owner_time ON audit_events(owner_id, created_at);

INSERT OR IGNORE INTO assistant_profiles (assistant_id,display_name,role,created_at,updated_at) VALUES ('companion','陪伴助手','companion',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));
INSERT OR IGNORE INTO assistant_profiles (assistant_id,display_name,role,created_at,updated_at) VALUES ('jarvis','Jarvis','productivity',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'));
