-- Local-only tags for fictional capture and note records.
CREATE TABLE synthetic_knowledge_tags (
 owner_id TEXT NOT NULL,
 object_type TEXT NOT NULL CHECK(object_type IN ('capture','note')),
 object_id TEXT NOT NULL,
 tag_key TEXT NOT NULL,
 tag_label TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,object_type,object_id,tag_key)
);
CREATE INDEX idx_synthetic_knowledge_tag_search ON synthetic_knowledge_tags(owner_id,tag_key,object_type,object_id);
