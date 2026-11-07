-- Synthetic, user-confirmed local reminder rules and acknowledgement history.
ALTER TABLE todos ADD COLUMN schedule_revision INTEGER NOT NULL DEFAULT 1 CHECK(schedule_revision>0);
CREATE TABLE IF NOT EXISTS synthetic_reminder_rules (
 id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL,
 todo_id TEXT NOT NULL REFERENCES todos(id),
 offset_minutes INTEGER NOT NULL CHECK(offset_minutes BETWEEN 0 AND 10080),
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(owner_id,todo_id,offset_minutes)
);
CREATE INDEX IF NOT EXISTS idx_synthetic_reminder_owner ON synthetic_reminder_rules(owner_id,enabled);
CREATE TABLE IF NOT EXISTS synthetic_reminder_acknowledgements (
 rule_id TEXT NOT NULL REFERENCES synthetic_reminder_rules(id),
 rule_version INTEGER NOT NULL,
 schedule_revision INTEGER NOT NULL,
 acknowledged_at TEXT NOT NULL,
 PRIMARY KEY(rule_id,rule_version,schedule_revision)
);
