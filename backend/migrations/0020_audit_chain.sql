-- Synthetic M1 audit prototype. A local chain alone cannot detect rollback.
CREATE TABLE audit_identity (
 singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
 vault_id TEXT NOT NULL,
 epoch TEXT NOT NULL
);
CREATE TABLE audit_chain (
 vault_id TEXT NOT NULL,
 epoch TEXT NOT NULL,
 seq INTEGER NOT NULL CHECK(seq > 0),
 event_json TEXT NOT NULL,
 prev_hash TEXT NOT NULL,
 entry_hash TEXT NOT NULL,
 PRIMARY KEY(vault_id, epoch, seq)
);
