-- User-supplied idempotency keys for local synthetic todo creation.
CREATE TABLE IF NOT EXISTS todo_create_requests (
 owner_id TEXT NOT NULL,
 assistant_id TEXT NOT NULL REFERENCES assistant_profiles(assistant_id),
 request_key TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 todo_id TEXT NOT NULL REFERENCES todos(id),
 created_at TEXT NOT NULL,
 PRIMARY KEY(owner_id,assistant_id,request_key)
);
