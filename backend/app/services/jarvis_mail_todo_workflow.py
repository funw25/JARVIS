"""Jarvis workflows joining synthetic mail and user-confirmed local todos."""

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, NotFound, validate_context
from .todo_service import TodoService


def _jarvis_only(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("This workflow is available only to Jarvis")


class JarvisMailTodoWorkflow:
    def __init__(self, todos: TodoService | None = None):
        self.todos = todos or TodoService()

    def create_from_mail(self, context: AssistantContext, mail_id: str, title: str,
                         description: str = "", priority: str = "normal", due_at: str | None = None,
                         *, confirmed_by_user: bool = False, idempotency_key: str | None = None) -> str:
        """Store a todo and its authorized mail reference in the same transaction."""
        _jarvis_only(context)
        return self.todos.create(context, title, description, priority, due_at,
                                 [{"type": "email", "id": mail_id, "relation": "derived_from"}],
                                 confirmed_by_user=confirmed_by_user, idempotency_key=idempotency_key)

    def get_mail_sources(self, context: AssistantContext, todo_id: str) -> list[dict]:
        """Return source metadata only for this owner's active Jarvis todo."""
        _jarvis_only(context)
        with connect() as conn:
            todo = conn.execute(
                "SELECT id FROM todos WHERE id=? AND owner_id=? AND created_by_assistant='jarvis' AND deleted_at IS NULL",
                (todo_id, context.owner_id),
            ).fetchone()
            if todo is None:
                raise NotFound("Todo not found")
            rows = conn.execute(
                "SELECT s.source_id,s.relation,m.id AS mail_id,m.subject,m.sender,m.triage_status,m.priority "
                "FROM todo_sources s LEFT JOIN mail_items m "
                "ON m.id=s.source_id AND m.owner_id=? AND m.source_kind='synthetic' "
                "WHERE s.todo_id=? AND s.source_type='email' ORDER BY s.source_id",
                (context.owner_id, todo_id),
            ).fetchall()
            return [{"type": "email", "source_id": row["source_id"], "relation": row["relation"],
                     "available": row["mail_id"] is not None,
                     "subject": row["subject"], "sender": row["sender"],
                     "triage_status": row["triage_status"], "priority": row["priority"]}
                    for row in rows]
