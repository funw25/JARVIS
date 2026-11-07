# Local API security boundary

## Implemented in B2/J3 guard

- Database access requires explicit JARVIS_DATA_MODE=synthetic. This is a development guard, not encryption or content classification.

- Business routes require a constant-time-checked bearer token from JARVIS_LOCAL_API_TOKEN, restricted to 32–128 URL-safe ASCII characters. Missing or malformed configuration fails closed with 503; length/format alone does not establish entropy.
- Owner identity comes from JARVIS_OWNER_ID (single-owner default) and cannot be supplied by the request.
- Assistant selection is a constrained X-Jarvis-Assistant header; all reads/writes still pass through domain authorization.
- API process is documented to bind to loopback; middleware accepts only loopback Host values, rejects duplicate Host/Origin headers, unexpected Origin and cross-site fetches, disables CORS, and sets no-store/nosniff/referrer headers.
- Pydantic request models reject unknown top-level fields and unknown SourceRef fields; owner is never accepted from a request body.
- Source references are checked against the current owner and assistant before persistence.
- Route errors avoid returning database paths or credentials; audit metadata excludes content.

## Not implemented

This is not a complete login system: no password/passkey, expiring session, token rotation, multi-user account, CSRF token flow, or OS credential vault. Local SQLite is not encrypted. The environment token is a temporary development gate and must be delivered to a client only through a secure native bridge in a future desktop shell. Do not expose the service to LAN or internet and do not store real personal content.

Pure local header validation is covered by backend/verification/verify_request_guard.py; FastAPI/HTTP integration remains untested until dependencies are available.
