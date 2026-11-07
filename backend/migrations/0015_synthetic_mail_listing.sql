-- Support bounded owner-scoped synthetic mail listing.
CREATE INDEX idx_synthetic_mail_owner_received ON mail_items(owner_id,source_kind,received_at DESC,id DESC);
