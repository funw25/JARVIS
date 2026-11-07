-- Link a newly started conversation to the accepted continuity snapshot it resumes.
ALTER TABLE conversations ADD COLUMN continuity_snapshot_id TEXT REFERENCES continuity_snapshots(id);
CREATE INDEX IF NOT EXISTS idx_conversations_continuity_snapshot ON conversations(continuity_snapshot_id);
