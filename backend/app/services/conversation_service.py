import json
from uuid import uuid4

from ..database import connect
from ..repositories.conversation_repository import ConversationRepository, ContinuityRepository
from ..security.assistant_context import AssistantContext, InvalidTransition, NotFound, validate_context
from ..security.source_access import validate_source_refs
from .memory_gateway import now_utc

class ConversationService:
    def __init__(self, repository: ConversationRepository | None = None):
        self.repository = repository or ConversationRepository()

    def create(self, context: AssistantContext, title: str = "") -> str:
        validate_context(context)
        conversation_id, now = str(uuid4()), now_utc()
        with connect() as conn:
            self.repository.create(conn, (conversation_id, context.owner_id, context.assistant_id, title.strip(), now, now, None))
            self._audit(conn, context, "conversation.created", "conversation", conversation_id, now)
        return conversation_id

    def list_for_context(self, context: AssistantContext) -> list[dict]:
        validate_context(context)
        with connect() as conn:
            return [dict(row) for row in self.repository.list_for_owner(conn, context.owner_id, context.assistant_id)]

    def history(self, context: AssistantContext, conversation_id: str) -> list[dict]:
        validate_context(context)
        with connect() as conn:
            conversation = self.repository.get(conn, context.owner_id, context.assistant_id, conversation_id)
            if conversation is None:
                raise NotFound("Conversation not found")
            return [dict(row) for row in self.repository.messages(conn, conversation_id)]

    def append_message(self, context: AssistantContext, conversation_id: str, role: str, body: str, source_refs: list[dict] | None = None) -> str:
        validate_context(context)
        if role not in ("user", "assistant"):
            raise ValueError("Only user and assistant messages can be appended")
        if not body.strip():
            raise ValueError("Message body cannot be empty")
        message_id, now = str(uuid4()), now_utc()
        with connect() as conn:
            conversation = self.repository.get(conn, context.owner_id, context.assistant_id, conversation_id)
            if conversation is None:
                raise NotFound("Conversation not found")
            refs = source_refs or []
            validate_source_refs(conn, context, refs)
            self.repository.add_message(conn, message_id, conversation_id, role, body, json.dumps(refs, ensure_ascii=False), now)
            self._audit(conn, context, "message.created", "message", message_id, now)
        return message_id

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, object_type: str, object_id: str, now: str) -> None:
        conn.execute(
            "INSERT INTO audit_events (id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?, 'user', ?, ?, ?, ?, '{}', ?)",
            (str(uuid4()), context.owner_id, context.owner_id, event, object_type, object_id, now),
        )

class ContinuityService:
    def __init__(self, conversations: ConversationRepository | None = None, snapshots: ContinuityRepository | None = None):
        self.conversations = conversations or ConversationRepository()
        self.snapshots = snapshots or ContinuityRepository()

    def create_draft(self, context: AssistantContext, conversation_id: str, summary: str, topics: list[str], open_items: list[str], source_refs: list[dict]) -> str:
        validate_context(context)
        if not summary.strip():
            raise ValueError("A continuity summary is required")
        snapshot_id, now = str(uuid4()), now_utc()
        with connect() as conn:
            conversation = self.conversations.get(conn, context.owner_id, context.assistant_id, conversation_id)
            if conversation is None:
                raise NotFound("Conversation not found")
            validate_source_refs(conn, context, source_refs)
            self.snapshots.create(conn, (snapshot_id, context.owner_id, context.assistant_id, conversation_id, summary.strip(), json.dumps(topics, ensure_ascii=False), json.dumps(open_items, ensure_ascii=False), json.dumps(source_refs, ensure_ascii=False), now))
            self._audit(conn, context, "continuity.draft_created", "continuity_snapshot", snapshot_id, now)
        return snapshot_id

    def discard(self, context: AssistantContext, snapshot_id: str) -> None:
        validate_context(context)
        now = now_utc()
        with connect() as conn:
            row = self.snapshots.get(conn, context.owner_id, context.assistant_id, snapshot_id)
            if row is None:
                raise NotFound("Continuity snapshot not found")
            if row["status"] != "draft":
                raise InvalidTransition("Only draft snapshots can be discarded")
            conn.execute("UPDATE continuity_snapshots SET status='discarded' WHERE id=?", (snapshot_id,))
            self._audit(conn, context, "continuity.discarded", "continuity_snapshot", snapshot_id, now)

    def accept_and_start(self, context: AssistantContext, snapshot_id: str, title: str = "续聊") -> tuple[str, dict]:
        """User approval and new-conversation creation happen in one transaction."""
        validate_context(context)
        now, new_conversation_id = now_utc(), str(uuid4())
        with connect() as conn:
            snapshot = self.snapshots.get(conn, context.owner_id, context.assistant_id, snapshot_id)
            if snapshot is None:
                raise NotFound("Continuity snapshot not found")
            if snapshot["status"] != "draft":
                raise InvalidTransition("Only draft snapshots can be accepted")
            self.snapshots.accept(conn, snapshot_id, now)
            self.conversations.create(conn, (new_conversation_id, context.owner_id, context.assistant_id, title.strip(), now, now, snapshot_id))
            self._audit(conn, context, "continuity.accepted_and_started", "continuity_snapshot", snapshot_id, now)
            payload = {
                "snapshot_id": snapshot_id,
                "summary": snapshot["summary"],
                "topics": json.loads(snapshot["topics_json"]),
                "open_items": json.loads(snapshot["open_items_json"]),
                "source_refs": json.loads(snapshot["source_refs_json"]),
            }
        return new_conversation_id, payload

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, object_type: str, object_id: str, now: str) -> None:
        conn.execute(
            "INSERT INTO audit_events (id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?, 'user', ?, ?, ?, ?, '{}', ?)",
            (str(uuid4()), context.owner_id, context.owner_id, event, object_type, object_id, now),
        )
