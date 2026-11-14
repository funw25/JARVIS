-- Old decisions did not record their generated work, so they cannot safely be replayed.
CREATE TABLE m1_materializations (
 decision_id TEXT PRIMARY KEY REFERENCES m1_decisions(id) ON DELETE CASCADE,
 state TEXT NOT NULL CHECK (state IN ('legacy_unknown','in_progress','complete')),
 claim_id TEXT,
 result_json TEXT,
 created_at TEXT NOT NULL,
 CHECK ((state = 'complete' AND result_json IS NOT NULL) OR
        (state != 'complete' AND result_json IS NULL))
);
INSERT INTO m1_materializations(decision_id, state, claim_id, result_json, created_at)
 SELECT id, 'legacy_unknown', NULL, NULL, strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM m1_decisions;
