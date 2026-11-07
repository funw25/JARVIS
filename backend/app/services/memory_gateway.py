from datetime import datetime, timezone
from uuid import uuid4

from ..database import connect
from ..repositories.memory_repository import MemoryRepository
from ..security.assistant_context import (AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context)
from ..security.source_access import validate_source_refs

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

class MemoryGateway:
    """The only supported application service for assistant memory reads/writes.

    HTTP access is only through the authenticated versioned API. Callers must provide
    a server-created AssistantContext; never construct it from an untrusted owner field.
    """
    def __init__(self, repository: MemoryRepository | None = None):
        self.repository = repository or MemoryRepository()

    def list_visible(self, context: AssistantContext) -> list[dict]:
        validate_context(context)
        with connect() as conn:
            rows = self.repository.visible_items(conn, context.owner_id, context.assistant_id)
            result = []
            for row in rows:
                result.append(dict(row))
                conn.execute(
                    "INSERT INTO audit_events (id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) "
                    "VALUES (?,?, 'assistant', ?, 'memory.read', 'memory', ?, ?, ?)",
                    (str(uuid4()), context.owner_id, context.assistant_id, row['id'], '{"via":"memory_gateway"}', now_utc()),
                )
            return result

    def create_candidate(self, context: AssistantContext, category: str, content: str, source_refs: list[dict], confidence: float | None = None) -> str:
        validate_context(context)
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        if confidence is not None and not 0 <= confidence <= 1:
            raise ValueError("Confidence must be between 0 and 1")
        namespace_kind = 'companion' if context.assistant_id == 'companion' else 'work'
        now, memory_id = now_utc(), str(uuid4())
        with connect() as conn:
            namespace = conn.execute(
                "SELECT id FROM memory_namespaces WHERE owner_id=? AND kind=?", (context.owner_id, namespace_kind)
            ).fetchone()
            if namespace is None:
                namespace_id = str(uuid4())
                conn.execute("INSERT INTO memory_namespaces(id,owner_id,kind,created_at) VALUES (?,?,?,?)",
                             (namespace_id, context.owner_id, namespace_kind, now))
            else:
                namespace_id = namespace['id']
            self.repository.insert_item(conn, (memory_id, namespace_id, context.assistant_id, category, content, confidence, None, now, now))
            validate_source_refs(conn, context, source_refs)
            for source in source_refs:
                source_type = source.get('type')
                source_id = source.get('id')
                if source_type not in ('message','memory','continuity_snapshot') or not isinstance(source_id, str) or not source_id:
                    raise ValueError("Each source requires a supported type and non-empty id")
                self.repository.add_source(conn, (memory_id, source_type, source_id, source.get('excerpt'), now))
            self._audit(conn, context, 'memory.candidate_created', 'memory', memory_id, now)
        return memory_id

    def approve(self, context: AssistantContext, memory_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            row = self.repository.get_item(conn, context.owner_id, memory_id)
            if row is None:
                raise NotFound("Memory not found")
            if row['created_by_assistant'] != context.assistant_id or row['namespace_kind'] != ('companion' if context.assistant_id == 'companion' else 'work'):
                raise AccessDenied("Only the originating assistant's owner context may approve this memory")
            if row['status'] != 'candidate':
                raise InvalidTransition("Only candidate memories can be approved")
            conn.execute("UPDATE memory_items SET status='approved',approved_at=?,updated_at=? WHERE id=?", (now, now, memory_id))
            self._audit(conn, context, 'memory.approved', 'memory', memory_id, now)

    def forget(self, context: AssistantContext, memory_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            row = self.repository.get_item(conn, context.owner_id, memory_id)
            if row is None:
                raise NotFound("Memory not found")
            if not self._may_manage(context, row):
                raise AccessDenied("Memory is not owned by this assistant")
            if row['status'] == 'deleted':
                raise NotFound("Memory not found")
            conn.execute("UPDATE memory_items SET status='forgotten',updated_at=? WHERE id=?", (now, memory_id))
            self._audit(conn, context, 'memory.forgotten', 'memory', memory_id, now)

    def delete(self, context: AssistantContext, memory_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            row = self.repository.get_item(conn, context.owner_id, memory_id)
            if row is None:
                raise NotFound("Memory not found")
            if not self._may_manage(context, row):
                raise AccessDenied("Memory is not owned by this assistant")
            conn.execute("DELETE FROM memory_access_grants WHERE memory_id=?", (memory_id,))
            conn.execute("DELETE FROM todo_sources WHERE source_type='memory' AND source_id=?", (memory_id,))
            conn.execute("DELETE FROM memory_items WHERE id=?", (memory_id,))
            self._audit(conn, context, 'memory.deleted', 'memory', memory_id, now)

    def grant(self, context: AssistantContext, target_assistant: str, *, memory_id: str | None = None, namespace_id: str | None = None) -> str:
        """Create a user-approved grant; callers must invoke only after explicit UI confirmation."""
        validate_context(context)
        if target_assistant not in ('companion','jarvis') or target_assistant == context.assistant_id:
            raise ValueError("Grant target must be the other assistant")
        if (memory_id is None) == (namespace_id is None):
            raise ValueError("Grant exactly one memory or namespace")
        now, grant_id = now_utc(), str(uuid4())
        with connect() as conn:
            if memory_id:
                row = self.repository.get_item(conn, context.owner_id, memory_id)
                if row is None:
                    raise NotFound("Memory not found")
                if not self._may_manage(context, row) or row['status'] != 'approved':
                    raise AccessDenied("Only an approved memory owned by this assistant can be shared")
                namespace_id = row['namespace_id']
            else:
                row = conn.execute("SELECT id,kind FROM memory_namespaces WHERE id=? AND owner_id=?", (namespace_id, context.owner_id)).fetchone()
                if row is None:
                    raise NotFound("Memory namespace not found")
                expected_kind = 'companion' if context.assistant_id == 'companion' else 'work'
                if row['kind'] != expected_kind:
                    raise AccessDenied("Only this assistant's namespace can be shared")
            self.repository.add_grant(conn, grant_id, context.owner_id, context.assistant_id, target_assistant, namespace_id, memory_id, now)
            self._audit(conn, context, 'memory.shared', 'memory_grant', grant_id, now, '{"target":"' + target_assistant + '"}')
        return grant_id

    def revoke_grant(self, context: AssistantContext, grant_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            cur = conn.execute("SELECT from_assistant FROM memory_access_grants WHERE id=? AND owner_id=? AND revoked_at IS NULL", (grant_id, context.owner_id)).fetchone()
            if cur is None:
                raise NotFound("Active memory grant not found")
            if cur['from_assistant'] != context.assistant_id:
                raise AccessDenied("Only the granting assistant's owner context may revoke this grant")
            self.repository.revoke_grant(conn, context.owner_id, grant_id, now)
            self._audit(conn, context, 'memory.share_revoked', 'memory_grant', grant_id, now)

    @staticmethod
    def _may_manage(context: AssistantContext, row) -> bool:
        expected_kind = 'companion' if context.assistant_id == 'companion' else 'work'
        return row['namespace_kind'] == expected_kind and row['created_by_assistant'] == context.assistant_id

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, object_type: str, object_id: str, now: str, metadata: str = '{}') -> None:
        conn.execute(
            "INSERT INTO audit_events (id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?, 'user', ?, ?, ?, ?, ?, ?)",
            (str(uuid4()), context.owner_id, context.assistant_id, event, object_type, object_id, metadata, now),
        )
