-- Synthetic checkpoint outbox; external dispatch remains disabled.
CREATE TABLE audit_outbox (
 snapshot_id TEXT NOT NULL,
 action TEXT NOT NULL CHECK(action = 'checkpoint_publish'),
 payload_json TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','retry','succeeded')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
 next_attempt_at TEXT,
 last_error TEXT,
 receipt TEXT,
 PRIMARY KEY(snapshot_id, action)
);
