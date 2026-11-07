# Backend development scaffold

Local-only FastAPI core with SQLite migrations, internal domain services, and versioned /api/v1 routes.

## Configuration

Explicitly set JARVIS_DATA_MODE=synthetic before startup; all other modes fail closed because encrypted personal storage is not implemented. Set JARVIS_LOCAL_API_TOKEN through a secure local environment/secret manager before using business routes. Never commit it, put it in a URL, browser storage, logs, or screenshots. Optional JARVIS_OWNER_ID selects the single local data owner; do not change it after real data exists. If the token is absent, business routes return 503.

Start with: uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765

The API requires Authorization: Bearer <token>; business operations also require X-Jarvis-Assistant: companion or jarvis. The middleware restricts Host/Origin to loopback. No CORS is enabled. See API_REFERENCE.md and SECURITY_BOUNDARY.md.

This is not account login or a production security boundary. No OS key vault or database encryption exists. Do not use real personal data; use synthetic data only.

## Verification

Run python backend/verification/verify_core.py from the project root to check Python syntax, SQLite migrations, and core domain access rules using temporary synthetic data. FastAPI route import is checked only when the declared requirements are installed.

Jarvis J1 synthetic mail service: app/services/synthetic_mail_service.py. Run python backend/verification/verify_jarvis_mail.py for local migration and approval-state checks. It does not connect an account or send mail.

Jarvis mail-to-todo source tracing: app/services/jarvis_mail_todo_workflow.py. Verify with python backend/verification/verify_mail_todo.py; uses temporary synthetic data only.

Jarvis synthetic capture and knowledge: app/services/synthetic_knowledge_service.py and app/services/jarvis_knowledge_todo_workflow.py. Verify with python backend/verification/verify_knowledge.py.

The storage-mode guard is not encryption and cannot recognize real content entered while synthetic mode is enabled. See ../docs/DATA_SECURITY_DESIGN.md.

Simulated model consent and reply-to-draft checks: python backend/verification/verify_model_consent.py and python backend/verification/verify_reply_draft.py. No external provider or send adapter exists.

Synthetic Jarvis overview: app/services/jarvis_overview_service.py. Verify with python backend/verification/verify_overview.py.

Migration reliability checks: python backend/verification/verify_migrations.py. This does not validate encrypted storage or power-loss recovery.

Synthetic owner-scoped export preview: app/services/synthetic_export_service.py. Verify with python backend/verification/verify_synthetic_export.py. This is unencrypted, does not write files, and cannot restore.

Synthetic read-only mailbox sync: app/services/synthetic_mail_sync_service.py. Verify with python backend/verification/verify_synthetic_sync.py; it has no OAuth, HTTP, real account or send adapter.

Synthetic mail rule checks: python backend/verification/verify_mail_rules.py. Rules run only after explicit local preview and user confirmation.


Actual loopback HTTP verification (requires installed project dependencies): run `python backend/verification/verify_http.py`. It uses a temporary synthetic database and temporary token; missing dependencies exit with status 2 and are reported as NOT_RUN. No external account is used. Importing routes alone is not an HTTP test.

Jarvis bounded local task search: `app/services/jarvis_task_query_service.py`; validate with `python backend/verification/verify_task_search.py`. This is also exposed through the local synthetic HTTP API.

Run `python backend/demo_jarvis.py` for a dependency-free fictional demonstration (or `--json` for structured output). It creates a fresh temporary synthetic database, removes it on exit, and never reads existing Jarvis data. Any approval shown is simulated; there is no actual sending. Validate using `python backend/verification/verify_demo.py`.

Task concurrency: `python backend/verification/verify_todo_concurrency.py`. Synthetic mailbox thread grouping: `python backend/verification/verify_mail_threads.py`. Both use temporary fictional data; the thread view is exposed by the local synthetic HTTP API and is not connected to a real account.

Run `python backend/verification/verify_all.py` for a consolidated synthetic verification report. It exits 0 only when all selected checks pass, 1 on failure, and 2 when checks are skipped without failure. It reports failures and skipped checks rather than hiding them; passing these checks does not mean application encryption or recovery is implemented.

Latest startup guidance and full verification instructions: ../docs/START_HERE.md. The isolated APSW encryption experiments are not connected to application storage.
