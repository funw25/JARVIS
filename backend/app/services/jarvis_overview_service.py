"""Jarvis local overview over synthetic data, without model or external calls."""
import heapq
from datetime import datetime, timedelta, timezone

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("This overview is available only to Jarvis")


def _utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, AttributeError, OverflowError):
        return None


def _task_bucket(value, now, local_zone, local_date):
    if value is None:
        return "without_due", None
    due = _utc(value)
    if due is None:
        return "invalid_due", None
    if due < now:
        return "overdue", due
    try:
        same_day = due.astimezone(local_zone).date() == local_date
    except OverflowError:
        same_day = False
    return ("due_today" if same_day else "future"), due


class JarvisOverviewService:
    def snapshot(self, context: AssistantContext, *, utc_offset_minutes: int = 0, limit: int = 5,
                 now: datetime | None = None) -> dict:
        _jarvis(context)
        if isinstance(utc_offset_minutes, bool) or not isinstance(utc_offset_minutes, int) or not -720 <= utc_offset_minutes <= 840:
            raise ValueError("UTC offset must be an integer between -720 and 840 minutes")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("Limit must be between 1 and 50")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Current time must include a timezone")
        try:
            now = now.astimezone(timezone.utc)
            local_date = now.astimezone(timezone(timedelta(minutes=utc_offset_minutes))).date()
        except (ValueError, OverflowError) as exc:
            raise ValueError("Current time is outside the supported range") from exc
        now_text = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        buckets = {"overdue": 0, "due_today": 0, "future": 0, "without_due": 0, "invalid_due": 0}
        order = {"overdue": 0, "due_today": 1, "future": 2, "invalid_due": 3, "without_due": 4}
        local_zone = timezone(timedelta(minutes=utc_offset_minutes))
        with connect() as conn:
            conn.execute("BEGIN")
            def task_entries():
                rows = conn.execute("SELECT id,title,status,priority,due_at FROM todos WHERE owner_id=? AND created_by_assistant='jarvis' AND status IN ('open','in_progress') AND deleted_at IS NULL", (context.owner_id,))
                for row in rows:
                    item = dict(row)
                    bucket, due = _task_bucket(item["due_at"], now, local_zone, local_date)
                    buckets[bucket] += 1
                    item["bucket"] = bucket
                    yield (bucket, due, item)
            tasks = heapq.nsmallest(limit, task_entries(),
                                    key=lambda entry: (order[entry[0]], entry[1] or datetime.max.replace(tzinfo=timezone.utc), entry[2]["id"]))
            mail_counts = conn.execute(
                "SELECT COALESCE(SUM(triage_status='new'),0) AS new_count, COALESCE(SUM(triage_status='needs_reply'),0) AS reply_count, COALESCE(SUM(priority='high'),0) AS high_count FROM mail_items WHERE owner_id=? AND triage_status IN ('new','needs_reply')",
                (context.owner_id,)).fetchone()
            mails = conn.execute("SELECT id,subject,sender,triage_status,priority,received_at FROM mail_items WHERE owner_id=? AND triage_status IN ('new','needs_reply') ORDER BY received_at DESC,id LIMIT ?", (context.owner_id, limit)).fetchall()
            capture_count = conn.execute("SELECT COUNT(*) FROM capture_items WHERE owner_id=? AND status='inbox' AND deleted_at IS NULL", (context.owner_id,)).fetchone()[0]
            captures = conn.execute("SELECT id,title,created_at FROM capture_items WHERE owner_id=? AND status='inbox' AND deleted_at IS NULL ORDER BY created_at DESC,id LIMIT ?", (context.owner_id, limit)).fetchall()
            note_count = conn.execute("SELECT COUNT(*) FROM knowledge_items WHERE owner_id=? AND deleted_at IS NULL", (context.owner_id,)).fetchone()[0]
            notes = conn.execute("SELECT id,title,updated_at FROM knowledge_items WHERE owner_id=? AND deleted_at IS NULL ORDER BY updated_at DESC,id LIMIT ?", (context.owner_id, limit)).fetchall()
            proposal_count = conn.execute("SELECT COUNT(*) FROM action_proposals WHERE owner_id=? AND kind='synthetic_email_send' AND status='pending' AND expires_at>?", (context.owner_id, now_text)).fetchone()[0]
            proposals = conn.execute("SELECT id,target_ref,expires_at FROM action_proposals WHERE owner_id=? AND kind='synthetic_email_send' AND status='pending' AND expires_at>? ORDER BY expires_at,id LIMIT ?", (context.owner_id, now_text, limit)).fetchall()
            preview_count = conn.execute("SELECT COUNT(*) FROM model_requests WHERE owner_id=? AND assistant_id='jarvis' AND provider_id='local_fake' AND status='pending' AND expires_at>?", (context.owner_id, now_text)).fetchone()[0]
            previews = conn.execute("SELECT id,purpose,source_type,source_id,expires_at FROM model_requests WHERE owner_id=? AND assistant_id='jarvis' AND provider_id='local_fake' AND status='pending' AND expires_at>? ORDER BY expires_at,id LIMIT ?", (context.owner_id, now_text, limit)).fetchall()
        return {
            "as_of": now_text, "local_date": local_date.isoformat(), "utc_offset_minutes": utc_offset_minutes,
            "todos": {"open": sum(buckets.values()), **buckets, "items": [entry[2] for entry in tasks]},
            "mail": {"new": mail_counts["new_count"], "needs_reply": mail_counts["reply_count"],
                     "high_priority": mail_counts["high_count"], "items": [dict(row) for row in mails]},
            "captures": {"inbox": capture_count, "items": [dict(row) for row in captures]},
            "knowledge": {"total": note_count, "items": [dict(row) for row in notes]},
            "send_approvals": {"pending": proposal_count, "items": [dict(row) for row in proposals]},
            "model_previews": {"pending": preview_count, "items": [dict(row) for row in previews]},
        }
