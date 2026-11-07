-- Synthetic-only sync runs bind to the fixture snapshot used when started.
ALTER TABLE synthetic_sync_runs ADD COLUMN dataset_fingerprint TEXT;
