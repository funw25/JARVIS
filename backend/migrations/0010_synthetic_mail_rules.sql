-- J9 local synthetic-mail organization rules and reversible change records.
ALTER TABLE mail_items ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0);
CREATE TABLE IF NOT EXISTS synthetic_mail_rules (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, sender_exact TEXT, subject_contains TEXT,
 target_triage TEXT, target_priority TEXT, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_synthetic_rules_owner ON synthetic_mail_rules(owner_id,enabled);
CREATE TABLE IF NOT EXISTS synthetic_rule_previews (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, rule_id TEXT NOT NULL REFERENCES synthetic_mail_rules(id), rule_version INTEGER NOT NULL,
 plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','applied','invalidated','expired')),
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL, applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_synthetic_rule_previews_owner ON synthetic_rule_previews(owner_id,status);
CREATE TABLE IF NOT EXISTS synthetic_rule_changes (
 preview_id TEXT NOT NULL REFERENCES synthetic_rule_previews(id), mail_id TEXT NOT NULL REFERENCES mail_items(id),
 before_triage TEXT NOT NULL, before_priority TEXT NOT NULL, after_triage TEXT NOT NULL, after_priority TEXT NOT NULL, applied_revision INTEGER NOT NULL,
 undo_status TEXT NOT NULL CHECK(undo_status IN ('pending','undone','conflict')), PRIMARY KEY(preview_id,mail_id)
);
