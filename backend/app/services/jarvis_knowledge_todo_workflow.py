"""Jarvis-only source-preserving todos from synthetic captures and notes."""
from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, NotFound, validate_context
from .todo_service import TodoService


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("This operation is available only to Jarvis")


class JarvisKnowledgeTodoWorkflow:
    def __init__(self, todos: TodoService | None = None):
        self.todos = todos or TodoService()

    def create_from_source(self, context: AssistantContext, source_type: str, source_id: str, title: str,
                           description: str = "", priority: str = "normal", due_at: str | None = None,
                           *, confirmed_by_user: bool = False, idempotency_key: str | None = None) -> str:
        _jarvis(context)
        if source_type not in ("capture", "note"):
            raise ValueError("Expected a capture or note")
        return self.todos.create(context, title, description, priority, due_at,
                                 [{"type": source_type, "id": source_id, "relation": "derived_from"}],
                                 confirmed_by_user=confirmed_by_user, idempotency_key=idempotency_key)

    def get_sources(self, context: AssistantContext, todo_id: str) -> list[dict]:
        _jarvis(context)
        with connect() as conn:
            todo = conn.execute("SELECT id FROM todos WHERE id=? AND owner_id=? AND created_by_assistant='jarvis' AND deleted_at IS NULL",
                                (todo_id, context.owner_id)).fetchone()
            if todo is None:
                raise NotFound("Todo not found")
            rows = conn.execute(
                "SELECT s.source_type,s.source_id,s.relation,c.id AS capture_id,c.title AS capture_title, "
                "n.id AS note_id,n.title AS note_title FROM todo_sources s "
                "LEFT JOIN capture_items c ON s.source_type='capture' AND c.id=s.source_id AND c.owner_id=? AND c.deleted_at IS NULL "
                "LEFT JOIN knowledge_items n ON s.source_type='note' AND n.id=s.source_id AND n.owner_id=? AND n.deleted_at IS NULL "
                "WHERE s.todo_id=? AND s.source_type IN ('capture','note') ORDER BY s.source_type,s.source_id",
                (context.owner_id, context.owner_id, todo_id),
            ).fetchall()
            return [{"type": row["source_type"], "source_id": row["source_id"], "relation": row["relation"],
                     "available": (row["capture_id"] is not None if row["source_type"] == "capture" else row["note_id"] is not None),
                     "title": (row["capture_title"] if row["source_type"] == "capture" else row["note_title"])}
                    for row in rows]
