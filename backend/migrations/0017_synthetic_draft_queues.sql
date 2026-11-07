-- Index local synthetic draft and pending proposal queues.
CREATE INDEX idx_synthetic_drafts_owner_updated ON mail_drafts(owner_id,updated_at DESC,id DESC);
CREATE INDEX idx_synthetic_proposals_owner_expiry ON action_proposals(owner_id,kind,status,expires_at,id);
