import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from ..database import connect
from ..repositories.todo_repository import TodoRepository
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context
from ..security.source_access import source_is_visible, synthetic_mail_is_visible, synthetic_knowledge_source_is_visible
from .memory_gateway import now_utc

_SOURCE_TYPES = {"message", "memory", "continuity_snapshot", "email", "capture", "note"}
_PRIORITIES = {"low", "normal", "high"}
_STATUSES = {"open", "in_progress", "done", "cancelled"}
_REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")

def _normalize_due(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("due_at must include a timezone")
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError("due_at must be an ISO 8601 timestamp within the supported UTC range") from exc

class TodoService:
    """Local todo use cases. Cross-assistant grants are read-only."""
    def __init__(self, repository: TodoRepository | None = None):
        self.repository = repository or TodoRepository()

    def create(self, context: AssistantContext, title: str, description: str = "", priority: str = "normal", due_at: str | None = None, sources: list[dict] | None = None, *, confirmed_by_user: bool = False, idempotency_key: str | None = None) -> str:
        validate_context(context)
        if not confirmed_by_user:
            raise AccessDenied("A todo must be explicitly confirmed by the user")
        if not isinstance(title, str):
            raise ValueError("Todo title must contain 1 to 500 characters")
        clean_title = title.strip()
        if not clean_title or len(clean_title) > 500:
            raise ValueError("Todo title must contain 1 to 500 characters")
        if not isinstance(description, str) or len(description) > 10000:
            raise ValueError("Todo description is too long")
        if not isinstance(priority, str) or priority not in _PRIORITIES:
            raise ValueError("Unknown priority")
        if due_at is not None and not isinstance(due_at, str):
            raise ValueError("due_at must be an ISO 8601 timestamp")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or _REQUEST_KEY.fullmatch(idempotency_key) is None):
            raise ValueError("Invalid idempotency key")
        if sources is not None and (not isinstance(sources, list) or len(sources) > 100):
            raise ValueError("Todo sources must be a list of at most 100 items")
        normalized_sources = []
        for source in sources or []:
            if not isinstance(source, dict):
                raise ValueError("Todo source must be an object")
            source_type, source_id = source.get("type"), source.get("id")
            relation = source.get("relation", "derived_from")
            if not isinstance(source_type, str) or source_type not in _SOURCE_TYPES or not isinstance(source_id, str) or not source_id.strip():
                raise ValueError("Todo source must have a supported type and non-empty id")
            if relation not in ("derived_from", "related_to", "follow_up"):
                raise ValueError("Unknown todo source relation")
            normalized_sources.append({"type": source_type, "id": source_id, "relation": relation})
        due = _normalize_due(due_at)
        payload = {"title": clean_title, "description": description, "priority": priority, "due_at": due, "sources": normalized_sources}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest() if idempotency_key is not None else None
        todo_id, now = str(uuid4()), now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if idempotency_key is not None:
                previous = conn.execute("SELECT todo_id,payload_hash FROM todo_create_requests WHERE owner_id=? AND assistant_id=? AND request_key=?", (context.owner_id,context.assistant_id,idempotency_key)).fetchone()
                if previous is not None:
                    if previous["payload_hash"] != digest:
                        raise InvalidTransition("Idempotency key was already used for different task content")
                    return previous["todo_id"]
            self.repository.create(conn, (todo_id, context.owner_id, context.assistant_id, clean_title, description, priority, due, now, now))
            for source in normalized_sources:
                source_type, source_id, relation = source["type"], source["id"], source["relation"]
                if source_type == "email":
                    visible = synthetic_mail_is_visible(conn, context, source_id)
                elif source_type in ("capture", "note"):
                    visible = synthetic_knowledge_source_is_visible(conn, context, source_type, source_id)
                else:
                    visible = source_is_visible(conn, context, source_type, source_id)
                if not visible:
                    raise NotFound("Todo source not found in this assistant's scope")
                self.repository.add_source(conn, (todo_id, source_type, source_id, relation))
            if idempotency_key is not None:
                conn.execute("INSERT INTO todo_create_requests(owner_id,assistant_id,request_key,payload_hash,todo_id,created_at) VALUES (?,?,?,?,?,?)", (context.owner_id,context.assistant_id,idempotency_key,digest,todo_id,now))
            self._audit(conn, context, "todo.created", todo_id, now)
        return todo_id

    def list(self, context: AssistantContext) -> list[dict]:
        validate_context(context)
        with connect() as conn:
            return [dict(row) for row in self.repository.list_visible(conn, context.owner_id, context.assistant_id)]

    def get(self, context: AssistantContext, todo_id: str) -> dict:
        validate_context(context)
        with connect() as conn:
            row = self.repository.get_visible(conn, context.owner_id, context.assistant_id, todo_id)
            if row is None:
                raise NotFound("Todo not found")
            sources = []
            if row["created_by_assistant"] == context.assistant_id:
                sources = conn.execute("SELECT source_type,source_id,relation FROM todo_sources WHERE todo_id=? ORDER BY source_type,source_id", (todo_id,)).fetchall()
            return {**dict(row), "sources": [dict(source) for source in sources]}

    def update(self, context: AssistantContext, todo_id: str, *, title: str | None = None, description: str | None = None, priority: str | None = None, due_at: str | None = None, clear_due_at: bool = False) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.repository.get_owned(conn, context.owner_id, context.assistant_id, todo_id)
            if row is None:
                raise NotFound("Todo not found")
            new_title = row["title"] if title is None else title.strip()
            new_description = row["description"] if description is None else description
            new_priority = row["priority"] if priority is None else priority
            new_due = None if clear_due_at else (row["due_at"] if due_at is None else _normalize_due(due_at))
            if not new_title or len(new_title) > 500:
                raise ValueError("Todo title must contain 1 to 500 characters")
            if len(new_description) > 10000:
                raise ValueError("Todo description is too long")
            if new_priority not in _PRIORITIES:
                raise ValueError("Unknown priority")
            schedule_changed = int(new_due != row["due_at"])
            conn.execute("UPDATE todos SET title=?,description=?,priority=?,due_at=?,updated_at=?,schedule_revision=schedule_revision+? WHERE id=?", (new_title,new_description,new_priority,new_due,now,schedule_changed,todo_id))
            self._audit(conn, context, "todo.updated", todo_id, now)

    def set_status(self, context: AssistantContext, todo_id: str, status: str) -> None:
        validate_context(context)
        if status not in _STATUSES:
            raise ValueError("Unknown todo status")
        now = now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.repository.get_owned(conn, context.owner_id, context.assistant_id, todo_id)
            if row is None:
                raise NotFound("Todo not found")
            if row["status"] == status:
                return
            if row["status"] == "cancelled" and status not in ("cancelled", "open"):
                raise InvalidTransition("A cancelled todo can only be reopened")
            completed_at = now if status == "done" else None
            reopened = int(row["status"] in ("done", "cancelled") and status in ("open", "in_progress"))
            conn.execute("UPDATE todos SET status=?,completed_at=?,updated_at=?,schedule_revision=schedule_revision+? WHERE id=?", (status,completed_at,now,reopened,todo_id))
            self._audit(conn, context, "todo.status_changed", todo_id, now)

    def delete(self, context: AssistantContext, todo_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.repository.get_owned(conn, context.owner_id, context.assistant_id, todo_id)
            if row is None:
                raise NotFound("Todo not found")
            conn.execute("UPDATE todos SET deleted_at=?,updated_at=? WHERE id=?", (now,now,todo_id))
            conn.execute("UPDATE todo_access_grants SET revoked_at=? WHERE todo_id=? AND revoked_at IS NULL", (now,todo_id))
            self._audit(conn, context, "todo.deleted", todo_id, now)

    def restore(self, context: AssistantContext, todo_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM todos WHERE id=? AND owner_id=? AND created_by_assistant=? AND deleted_at IS NOT NULL", (todo_id,context.owner_id,context.assistant_id)).fetchone()
            if row is None:
                raise NotFound("Deleted todo not found")
            conn.execute("UPDATE todos SET deleted_at=NULL,updated_at=?,schedule_revision=schedule_revision+1 WHERE id=?", (now,todo_id))
            self._audit(conn, context, "todo.restored", todo_id, now)

    def share_readonly(self, context: AssistantContext, todo_id: str, target_assistant: str, *, confirmed_by_user: bool = False) -> str:
        validate_context(context)
        if not confirmed_by_user:
            raise AccessDenied("Sharing requires explicit user confirmation")
        if target_assistant not in ("companion", "jarvis") or target_assistant == context.assistant_id:
            raise ValueError("Grant target must be the other assistant")
        grant_id, now = str(uuid4()), now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            todo = self.repository.get_owned(conn, context.owner_id, context.assistant_id, todo_id)
            if todo is None:
                raise NotFound("Todo not found")
            existing = conn.execute("SELECT id FROM todo_access_grants WHERE owner_id=? AND todo_id=? AND from_assistant=? AND to_assistant=? AND revoked_at IS NULL ORDER BY granted_at,id LIMIT 1", (context.owner_id,todo_id,context.assistant_id,target_assistant)).fetchone()
            if existing is not None:
                return existing["id"]
            conn.execute("INSERT INTO todo_access_grants (id,owner_id,todo_id,from_assistant,to_assistant,granted_at,revoked_at) VALUES (?,?,?,?,?,?,NULL)", (grant_id,context.owner_id,todo_id,context.assistant_id,target_assistant,now))
            self._audit(conn, context, "todo.shared_readonly", todo_id, now, '{"target":"'+target_assistant+'"}')
        return grant_id

    def revoke_share(self, context: AssistantContext, grant_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            grant = conn.execute("SELECT id,todo_id,to_assistant FROM todo_access_grants WHERE id=? AND owner_id=? AND from_assistant=? AND revoked_at IS NULL", (grant_id,context.owner_id,context.assistant_id)).fetchone()
            if grant is None:
                raise NotFound("Active todo share not found")
            # Older synthetic databases may contain duplicate active grants.
            conn.execute("UPDATE todo_access_grants SET revoked_at=? WHERE owner_id=? AND todo_id=? AND from_assistant=? AND to_assistant=? AND revoked_at IS NULL", (now,context.owner_id,grant["todo_id"],context.assistant_id,grant["to_assistant"]))
            self._audit(conn, context, "todo.share_revoked", grant_id, now)

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, todo_id: str, now: str, metadata: str = "{}") -> None:
        conn.execute("INSERT INTO audit_events (id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?, 'user', ?, ?, 'todo', ?, ?, ?)", (str(uuid4()),context.owner_id,context.owner_id,event,todo_id,metadata,now))
