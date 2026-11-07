-- Permit only source kinds whose owner/assistant scope is checked by source_access.py.
ALTER TABLE memory_sources RENAME TO memory_sources_v1;
CREATE TABLE memory_sources (
 memory_id TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
 source_type TEXT NOT NULL CHECK (source_type IN ('message','capture','user_entry','import','memory','continuity_snapshot')),
 source_id TEXT NOT NULL,
 excerpt TEXT,
 created_at TEXT NOT NULL,
 PRIMARY KEY(memory_id, source_type, source_id)
);
INSERT INTO memory_sources(memory_id,source_type,source_id,excerpt,created_at)
 SELECT memory_id,source_type,source_id,excerpt,created_at FROM memory_sources_v1;
DROP TABLE memory_sources_v1;
