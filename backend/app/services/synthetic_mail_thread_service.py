"""Read-only views of local synthetic mail threads; no real mailbox access."""
from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, NotFound, validate_context


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Mail threads are available only to Jarvis")


def _limit(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError("Limit must be an integer from 1 to 100")
    return value


class SyntheticMailThreadService:
    """Group provider messages by both connection and thread; manual messages stand alone."""

    def list_threads(self, context: AssistantContext, *, limit: int = 25) -> dict:
        _jarvis(context)
        _limit(limit)
        with connect() as conn:
            rows = conn.execute("""
                WITH owned AS (
                    SELECT m.id,m.sender,m.subject,m.received_at,m.triage_status,m.priority,
                           c.id AS connection_id,l.thread_id,
                           CASE WHEN c.id IS NULL THEN 'manual' ELSE 'provider' END AS kind,
                           CASE WHEN c.id IS NULL THEN m.id ELSE c.id END AS group_a,
                           CASE WHEN c.id IS NULL THEN '' ELSE l.thread_id END AS group_b
                    FROM mail_items m
                    LEFT JOIN synthetic_mail_links l ON l.mail_id=m.id
                    LEFT JOIN synthetic_mail_connections c ON c.id=l.connection_id AND c.owner_id=m.owner_id
                    WHERE m.owner_id=? AND m.source_kind='synthetic'
                      AND (l.connection_id IS NULL OR c.id IS NOT NULL)
                ), ranked AS (
                    SELECT *, row_number() OVER (PARTITION BY kind,group_a,group_b ORDER BY received_at DESC,id DESC) AS rank,
                           count(*) OVER (PARTITION BY kind,group_a,group_b) AS message_count,
                           sum(CASE WHEN triage_status='needs_reply' THEN 1 ELSE 0 END) OVER (PARTITION BY kind,group_a,group_b) AS needs_reply_count,
                           max(CASE WHEN priority='high' THEN 1 ELSE 0 END) OVER (PARTITION BY kind,group_a,group_b) AS high_priority
                    FROM owned
                )
                SELECT kind,connection_id,thread_id,id AS latest_mail_id,sender AS latest_sender,
                       subject AS latest_subject,received_at AS latest_received_at,message_count,needs_reply_count,high_priority
                FROM ranked WHERE rank=1 ORDER BY received_at DESC,id DESC LIMIT ?
            """, (context.owner_id, limit + 1)).fetchall()
        return {"items": [dict(row) for row in rows[:limit]], "truncated": len(rows) > limit}

    def get_provider_thread(self, context: AssistantContext, connection_id: str, thread_id: str, *, limit: int = 50) -> dict:
        _jarvis(context)
        _limit(limit)
        if not isinstance(connection_id, str) or not connection_id or len(connection_id) > 128 or not isinstance(thread_id, str) or not thread_id or len(thread_id) > 100:
            raise ValueError("Invalid synthetic thread identity")
        with connect() as conn:
            rows = conn.execute("""
                SELECT m.id,m.sender,m.subject,m.triage_status,m.priority,m.received_at,m.updated_at
                FROM synthetic_mail_links l
                JOIN synthetic_mail_connections c ON c.id=l.connection_id AND c.owner_id=?
                JOIN mail_items m ON m.id=l.mail_id AND m.owner_id=c.owner_id AND m.source_kind='synthetic'
                WHERE l.connection_id=? AND l.thread_id=? ORDER BY m.received_at DESC,m.id DESC LIMIT ?
            """, (context.owner_id, connection_id, thread_id, limit + 1)).fetchall()
        if not rows:
            raise NotFound("Synthetic thread not found")
        return {"connection_id": connection_id, "thread_id": thread_id,
                "items": [dict(row) for row in rows[:limit]], "truncated": len(rows) > limit}
