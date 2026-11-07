-- J1 synthetic mail workflow. Real account integration is intentionally absent.
CREATE TABLE IF NOT EXISTS mail_items (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, source_kind TEXT NOT NULL CHECK (source_kind='synthetic'),
 sender TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 triage_status TEXT NOT NULL DEFAULT 'new' CHECK (triage_status IN ('new','needs_reply','waiting','archived')),
 priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high')),
 received_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mail_owner_triage ON mail_items(owner_id,triage_status,received_at);
CREATE TABLE IF NOT EXISTS mail_drafts (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, mail_id TEXT NOT NULL REFERENCES mail_items(id),
 account_address TEXT NOT NULL, recipients_json TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK (version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mail_drafts_owner_mail ON mail_drafts(owner_id,mail_id);
