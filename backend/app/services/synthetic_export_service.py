"""Owner-scoped synthetic export and validation; not an encrypted backup or restore."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context
from ..security.storage_policy import require_synthetic_storage
from .synthetic_knowledge_service import _synthetic_url
from .synthetic_mail_service import _address, _recipients

_FORMAT = "jarvis-synthetic-export"
_VERSION = 6
_MAX_RECORDS = 10000
_MAX_BYTES = 5_000_000
_EXCLUDED_V1 = ["companion", "conversations", "memory", "model_requests", "suggestions", "approvals", "audit", "credentials", "deleted_items"]
_EXCLUDED_V2 = _EXCLUDED_V1 + ["synthetic_connections", "sync_runs"]
_EXCLUDED_V3 = _EXCLUDED_V2 + ["synthetic_rules", "rule_previews", "rule_changes"]
_EXCLUDED_V4 = _EXCLUDED_V3 + ["todo_idempotency"]
_EXCLUDED_V5 = _EXCLUDED_V4 + ["reminder_rules", "reminder_acknowledgements"]
_EXCLUDED = _EXCLUDED_V5 + ["knowledge_tags"]
_FIELDS = {
    "emails": {"id", "sender", "subject", "body", "triage_status", "priority", "received_at", "updated_at"},
    "drafts": {"id", "mail_id", "account_address", "recipients", "subject", "body", "version", "created_at", "updated_at"},
    "captures": {"id", "title", "content", "source_url", "status", "created_at", "updated_at"},
    "notes": {"id", "title", "body", "source_capture_id", "created_at", "updated_at"},
    "todos": {"id", "title", "description", "status", "priority", "due_at", "completed_at", "created_at", "updated_at"},
    "todo_sources": {"todo_id", "source_type", "source_id", "relation"},
}


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Synthetic export is available only to Jarvis")


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _preflight_payload(value: object) -> None:
    """Bound in-memory preview data before JSON encoding; reject cycles/deep nesting."""
    stack = [(value, 0)]
    nodes = 0
    string_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 500_000 or depth > 8:
            raise ValueError("Synthetic export structure exceeds limits")
        if isinstance(item, str):
            if len(item) > _MAX_BYTES - string_bytes:
                raise ValueError("Synthetic export exceeds size limit")
            try:
                string_bytes += len(item.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("Invalid export text") from exc
            if string_bytes > _MAX_BYTES:
                raise ValueError("Synthetic export exceeds size limit")
        elif isinstance(item, dict):
            if len(item) > _MAX_RECORDS or len(stack) + 2 * len(item) > 500_000:
                raise ValueError("Synthetic export structure exceeds limits")
            if any(not isinstance(key, str) for key in item):
                raise ValueError("Invalid synthetic export field name")
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > _MAX_RECORDS or len(stack) + len(item) > 500_000:
                raise ValueError("Synthetic export structure exceeds limits")
            stack.extend((child, depth + 1) for child in item)
        elif item is None or type(item) is bool:
            pass
        elif type(item) is int and item.bit_length() <= 64:
            pass
        else:
            raise ValueError("Invalid synthetic export value")


def _id(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("Invalid record identifier")
    try:
        valid = str(UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("Invalid record identifier")


def _string(value: str, limit: int, required: bool = False) -> None:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError("Invalid export field")


def _date(value: str | None, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise ValueError("Invalid export timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid export timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Export timestamps must include a timezone")


class SyntheticExportService:
    def build(self, context: AssistantContext) -> dict:
        _jarvis(context)
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            estimates = [
                "SELECT COALESCE(SUM(length(CAST(sender AS BLOB))+length(CAST(subject AS BLOB))+length(CAST(body AS BLOB))),0) FROM mail_items WHERE owner_id=? AND source_kind='synthetic'",
                "SELECT COALESCE(SUM(length(CAST(account_address AS BLOB))+length(CAST(recipients_json AS BLOB))+length(CAST(subject AS BLOB))+length(CAST(body AS BLOB))),0) FROM mail_drafts WHERE owner_id=?",
                "SELECT COALESCE(SUM(length(CAST(title AS BLOB))+length(CAST(content AS BLOB))+COALESCE(length(CAST(source_url AS BLOB)),0)),0) FROM capture_items WHERE owner_id=? AND deleted_at IS NULL",
                "SELECT COALESCE(SUM(length(CAST(title AS BLOB))+length(CAST(body AS BLOB))),0) FROM knowledge_items WHERE owner_id=? AND deleted_at IS NULL",
                "SELECT COALESCE(SUM(length(CAST(title AS BLOB))+length(CAST(description AS BLOB))),0) FROM todos WHERE owner_id=? AND created_by_assistant='jarvis' AND deleted_at IS NULL",
            ]
            if sum(conn.execute(sql, (context.owner_id,)).fetchone()[0] for sql in estimates) > _MAX_BYTES:
                raise ValueError("Synthetic export exceeds size limit")
            def rows(sql):
                values = conn.execute(sql, (context.owner_id, _MAX_RECORDS + 1)).fetchall()
                if len(values) > _MAX_RECORDS:
                    raise ValueError("Synthetic export exceeds record limit")
                return [dict(item) for item in values]
            emails = rows("SELECT id,sender,subject,body,triage_status,priority,received_at,updated_at FROM mail_items WHERE owner_id=? AND source_kind='synthetic' ORDER BY id LIMIT ?")
            drafts = rows("SELECT d.id,d.mail_id,d.account_address,d.recipients_json,d.subject,d.body,d.version,d.created_at,d.updated_at FROM mail_drafts d JOIN mail_items m ON m.id=d.mail_id AND m.owner_id=d.owner_id AND m.source_kind='synthetic' WHERE d.owner_id=? ORDER BY d.id LIMIT ?")
            captures = rows("SELECT id,title,content,source_url,status,created_at,updated_at FROM capture_items WHERE owner_id=? AND deleted_at IS NULL ORDER BY id LIMIT ?")
            notes = rows("SELECT id,title,body,source_capture_id,created_at,updated_at FROM knowledge_items WHERE owner_id=? AND deleted_at IS NULL ORDER BY id LIMIT ?")
            todos = rows("SELECT id,title,description,status,priority,due_at,completed_at,created_at,updated_at FROM todos WHERE owner_id=? AND created_by_assistant='jarvis' AND deleted_at IS NULL ORDER BY id LIMIT ?")
            sources = rows("SELECT s.todo_id,s.source_type,s.source_id,s.relation FROM todo_sources s JOIN todos t ON t.id=s.todo_id WHERE t.owner_id=? AND t.created_by_assistant='jarvis' AND t.deleted_at IS NULL ORDER BY s.todo_id,s.source_type,s.source_id LIMIT ?")
            total_drafts = conn.execute("SELECT COUNT(*) FROM mail_drafts WHERE owner_id=?", (context.owner_id,)).fetchone()[0]
            omitted_drafts = total_drafts - len(drafts)
            for draft in drafts:
                draft["recipients"] = json.loads(draft.pop("recipients_json"))
            ids = {"email": {row["id"] for row in emails}, "capture": {row["id"] for row in captures}, "note": {row["id"] for row in notes}}
            omitted_sources = len([row for row in sources if row["source_type"] not in ids or row["source_id"] not in ids[row["source_type"]]])
            sources = [row for row in sources if row["source_type"] in ids and row["source_id"] in ids[row["source_type"]]]
            unavailable_note_sources = 0
            for note in notes:
                if note["source_capture_id"] is not None and note["source_capture_id"] not in ids["capture"]:
                    note["source_capture_id"] = None
                    unavailable_note_sources += 1
            sections = {"emails": emails, "drafts": drafts, "captures": captures, "notes": notes, "todos": todos, "todo_sources": sources}
            body = {"format": _FORMAT, "version": _VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "sections": sections,
                    "manifest": {"counts": {key: len(value) for key, value in sections.items()},
                                 "omitted_source_links": omitted_sources, "unavailable_note_sources": unavailable_note_sources, "omitted_drafts": omitted_drafts,
                                 "excluded": list(_EXCLUDED)}}
            raw = _canonical(body)
            if len(raw) > _MAX_BYTES:
                raise ValueError("Synthetic export exceeds size limit")
            bundle = {**body, "content_sha256": hashlib.sha256(raw).hexdigest()}
            self._validate(bundle)
            conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,'synthetic_export.created','export',?,'{}',?)",
                         (str(uuid4()), context.owner_id, context.owner_id, str(uuid4()), body["created_at"]))
        return bundle

    def preview(self, context: AssistantContext, bundle: dict) -> dict:
        _jarvis(context)
        require_synthetic_storage()
        self._validate(bundle)
        return {"format": _FORMAT, "version": bundle["version"], "created_at": bundle["created_at"],
                "counts": dict(bundle["manifest"]["counts"]),
                "omitted_source_links": bundle["manifest"]["omitted_source_links"],
                "unavailable_note_sources": bundle["manifest"]["unavailable_note_sources"],
                "omitted_drafts": bundle["manifest"]["omitted_drafts"],
                "excluded": list(bundle["manifest"]["excluded"]), "encrypted": False, "restore_available": False}

    @staticmethod
    def _validate(bundle: dict) -> None:
        if not isinstance(bundle, dict) or set(bundle) != {"format", "version", "created_at", "sections", "manifest", "content_sha256"}:
            raise ValueError("Invalid synthetic export envelope")
        if bundle["format"] != _FORMAT or type(bundle["version"]) is not int or bundle["version"] not in (1, 2, 3, 4, 5, _VERSION):
            raise ValueError("Unsupported synthetic export format")
        _date(bundle["created_at"])
        sections, manifest = bundle["sections"], bundle["manifest"]
        if not isinstance(sections, dict) or set(sections) != set(_FIELDS) or not isinstance(manifest, dict) or set(manifest) != {"counts", "omitted_source_links", "unavailable_note_sources", "omitted_drafts", "excluded"}:
            raise ValueError("Invalid synthetic export sections or manifest")
        expected_excluded = {1: _EXCLUDED_V1, 2: _EXCLUDED_V2, 3: _EXCLUDED_V3, 4: _EXCLUDED_V4, 5: _EXCLUDED_V5, 6: _EXCLUDED}[bundle["version"]]
        if manifest["excluded"] != expected_excluded or not isinstance(manifest["counts"], dict) or set(manifest["counts"]) != set(_FIELDS):
            raise ValueError("Invalid synthetic export manifest")
        for name, fields in _FIELDS.items():
            records = sections[name]
            if not isinstance(records, list) or len(records) > _MAX_RECORDS or type(manifest["counts"][name]) is not int or manifest["counts"][name] != len(records):
                raise ValueError("Invalid synthetic export record count")
            if any(not isinstance(row, dict) or set(row) != fields for row in records):
                raise ValueError("Invalid synthetic export record")
        for key in ("omitted_source_links", "unavailable_note_sources", "omitted_drafts"):
            if type(manifest[key]) is not int or manifest[key] < 0:
                raise ValueError("Invalid omission count")
        _preflight_payload(bundle)
        body = {key: bundle[key] for key in ("format", "version", "created_at", "sections", "manifest")}
        raw = _canonical(body)
        digest = bundle["content_sha256"]
        if len(raw) > _MAX_BYTES or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
            raise ValueError("Synthetic export integrity check failed")
        ids = {}
        for name in ("emails", "drafts", "captures", "notes", "todos"):
            values = [row["id"] for row in sections[name]]
            for value in values:
                _id(value)
            if len(values) != len(set(values)):
                raise ValueError("Duplicate record identifier")
            ids[name] = set(values)
        for row in sections["emails"]:
            _string(row["sender"], 320, True); _address(row["sender"]); _string(row["subject"], 500, True); _string(row["body"], 100000)
            _date(row["received_at"]); _date(row["updated_at"])
            if row["triage_status"] not in ("new", "needs_reply", "waiting", "archived") or row["priority"] not in ("low", "normal", "high"):
                raise ValueError("Invalid email state")
        for row in sections["drafts"]:
            _id(row["mail_id"]); _string(row["account_address"], 320, True); _address(row["account_address"])
            if not isinstance(row["recipients"], list) or any(not isinstance(value, str) or len(value) > 320 for value in row["recipients"]):
                raise ValueError("Invalid synthetic recipients")
            _recipients(row["recipients"])
            _string(row["subject"], 500, True); _string(row["body"], 100000); _date(row["created_at"]); _date(row["updated_at"])
            if row["mail_id"] not in ids["emails"] or type(row["version"]) is not int or row["version"] < 1:
                raise ValueError("Invalid draft source or version")
        for row in sections["captures"]:
            _string(row["title"], 500, True); _string(row["content"], 100000); _synthetic_url(row["source_url"])
            _date(row["created_at"]); _date(row["updated_at"])
            if row["status"] not in ("inbox", "processed", "archived"):
                raise ValueError("Invalid capture status")
        for row in sections["notes"]:
            _string(row["title"], 500, True); _string(row["body"], 100000, True); _date(row["created_at"]); _date(row["updated_at"])
            if row["source_capture_id"] is not None:
                _id(row["source_capture_id"])
                if row["source_capture_id"] not in ids["captures"]:
                    raise ValueError("Invalid note source")
        for row in sections["todos"]:
            _string(row["title"], 500, True); _string(row["description"], 10000); _date(row["created_at"]); _date(row["updated_at"])
            _date(row["due_at"], True); _date(row["completed_at"], True)
            if row["status"] not in ("open", "in_progress", "done", "cancelled") or row["priority"] not in ("low", "normal", "high"):
                raise ValueError("Invalid todo state")
        links = set()
        for row in sections["todo_sources"]:
            _id(row["todo_id"]); _id(row["source_id"])
            source_sets = {"email": ids["emails"], "capture": ids["captures"], "note": ids["notes"]}
            if row["todo_id"] not in ids["todos"] or not isinstance(row["source_type"], str) or row["source_type"] not in source_sets or row["source_id"] not in source_sets[row["source_type"]] or row["relation"] not in ("derived_from", "related_to", "follow_up"):
                raise ValueError("Invalid todo source")
            link = (row["todo_id"], row["source_type"], row["source_id"])
            if link in links:
                raise ValueError("Duplicate todo source")
            links.add(link)
