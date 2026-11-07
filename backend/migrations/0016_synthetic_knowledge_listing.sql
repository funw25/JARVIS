-- Bounded listing for owner-scoped local synthetic captures and notes.
CREATE INDEX idx_capture_owner_visible_updated ON capture_items(owner_id,deleted_at,updated_at DESC,id DESC);
CREATE INDEX idx_knowledge_owner_visible_updated ON knowledge_items(owner_id,deleted_at,updated_at DESC,id DESC);
