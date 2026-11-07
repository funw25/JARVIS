"""User-confirmed local reminder rules for fictional Jarvis tasks.

No scheduler, desktop notifications, network calls, or real user data.
"""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context
from .memory_gateway import now_utc


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Reminders are available only to Jarvis")


def _moment(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Current time must include a timezone")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Current time is outside the supported range") from exc


def _due(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("Missing timezone")
        return result.astimezone(timezone.utc)
    except (AttributeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid task due time") from exc


def _token(row) -> str:
    payload = {"rule_id": row["rule_id"], "rule_version": row["rule_version"],
               "todo_id": row["todo_id"], "schedule_revision": row["schedule_revision"],
               "due_at": row["due_at"], "offset_minutes": row["offset_minutes"], "title": row["title"]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _trigger(row) -> datetime:
    try:
        return _due(row["due_at"]) - timedelta(minutes=row["offset_minutes"])
    except OverflowError as exc:
        raise ValueError("Reminder time is outside supported range") from exc


class SyntheticReminderService:
    def create_rule(self, context: AssistantContext, todo_id: str, offset_minutes: int, *, confirmed_by_user: bool = False) -> str:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        if not isinstance(todo_id, str) or not todo_id or len(todo_id) > 128:
            raise ValueError("Invalid task identifier")
        if type(offset_minutes) is not int or not 0 <= offset_minutes <= 10080:
            raise ValueError("Reminder offset must be 0 to 10080 minutes")
        rule_id, now = str(uuid4()), now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT id,status,due_at FROM todos WHERE id=? AND owner_id=? AND created_by_assistant='jarvis' AND deleted_at IS NULL", (todo_id, context.owner_id)).fetchone()
            if task is None:
                raise NotFound("Task not found")
            if task["status"] not in ("open", "in_progress") or task["due_at"] is None:
                raise InvalidTransition("An active task with a due time is required")
            _trigger({"due_at": task["due_at"], "offset_minutes": offset_minutes})
            existing = conn.execute("SELECT id,enabled FROM synthetic_reminder_rules WHERE owner_id=? AND todo_id=? AND offset_minutes=?", (context.owner_id, todo_id, offset_minutes)).fetchone()
            if existing is not None:
                if not existing["enabled"]:
                    raise InvalidTransition("Reminder exists but is disabled; enable it explicitly")
                return existing["id"]
            conn.execute("INSERT INTO synthetic_reminder_rules(id,owner_id,todo_id,offset_minutes,enabled,version,created_at,updated_at) VALUES (?,?,?,?,1,1,?,?)", (rule_id, context.owner_id, todo_id, offset_minutes, now, now))
            self._audit(conn, context, "reminder.rule_created", rule_id, now)
        return rule_id

    def set_enabled(self, context: AssistantContext, rule_id: str, enabled: bool, *, confirmed_by_user: bool = False) -> None:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        if type(enabled) is not bool:
            raise ValueError("Enabled must be a boolean")
        now = now_utc()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rule = conn.execute("SELECT id,enabled FROM synthetic_reminder_rules WHERE id=? AND owner_id=?", (rule_id, context.owner_id)).fetchone()
            if rule is None:
                raise NotFound("Reminder rule not found")
            if bool(rule["enabled"]) == enabled:
                return
            conn.execute("UPDATE synthetic_reminder_rules SET enabled=?,version=version+1,updated_at=? WHERE id=? AND owner_id=?", (int(enabled), now, rule_id, context.owner_id))
            self._audit(conn, context, "reminder.enabled_changed", rule_id, now)

    def list_rules(self, context: AssistantContext) -> list[dict]:
        _jarvis(context)
        with connect() as conn:
            rows = conn.execute("SELECT r.id,r.todo_id,r.offset_minutes,r.enabled,r.version,t.title,t.due_at,t.status FROM synthetic_reminder_rules r JOIN todos t ON t.id=r.todo_id AND t.owner_id=r.owner_id WHERE r.owner_id=? AND t.created_by_assistant='jarvis' AND t.deleted_at IS NULL ORDER BY t.due_at,r.id LIMIT 10001", (context.owner_id,)).fetchall()
        if len(rows) > 10000:
            raise ValueError("Too many reminder rules")
        return [dict(row) for row in rows]

    def due(self, context: AssistantContext, *, now: datetime | None = None, limit: int = 25) -> dict:
        _jarvis(context)
        current = _moment(now)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Reminder limit must be from 1 to 100")
        with connect() as conn:
            rows = conn.execute("""
                SELECT r.id AS rule_id,r.version AS rule_version,r.offset_minutes,t.id AS todo_id,
                       t.title,t.due_at,t.schedule_revision,a.acknowledged_at
                FROM synthetic_reminder_rules r
                JOIN todos t ON t.id=r.todo_id AND t.owner_id=r.owner_id
                LEFT JOIN synthetic_reminder_acknowledgements a ON a.rule_id=r.id AND a.rule_version=r.version AND a.schedule_revision=t.schedule_revision
                WHERE r.owner_id=? AND r.enabled=1 AND t.created_by_assistant='jarvis'
                  AND t.deleted_at IS NULL AND t.status IN ('open','in_progress') AND t.due_at IS NOT NULL
                ORDER BY t.due_at,r.id LIMIT 10001
            """, (context.owner_id,)).fetchall()
        if len(rows) > 10000:
            raise ValueError("Too many active reminder rules")
        due_items = []
        for row in rows:
            trigger = _trigger(row)
            if row["acknowledged_at"] is None and trigger <= current:
                due_items.append({"rule_id": row["rule_id"], "todo_id": row["todo_id"], "title": row["title"],
                                  "due_at": row["due_at"], "offset_minutes": row["offset_minutes"],
                                  "trigger_at": trigger.isoformat(timespec="seconds").replace("+00:00", "Z"), "token": _token(row)})
        due_items.sort(key=lambda item: (item["trigger_at"], item["due_at"], item["rule_id"]))
        return {"items": due_items[:limit], "truncated": len(due_items) > limit}

    def acknowledge(self, context: AssistantContext, rule_id: str, token: str, *, confirmed_by_user: bool = False, now: datetime | None = None) -> dict:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        current = _moment(now)
        if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise ValueError("Invalid reminder token")
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT r.id AS rule_id,r.version AS rule_version,r.offset_minutes,r.enabled,t.id AS todo_id,
                       t.title,t.due_at,t.status,t.deleted_at,t.schedule_revision,a.acknowledged_at
                FROM synthetic_reminder_rules r JOIN todos t ON t.id=r.todo_id AND t.owner_id=r.owner_id
                LEFT JOIN synthetic_reminder_acknowledgements a ON a.rule_id=r.id AND a.rule_version=r.version AND a.schedule_revision=t.schedule_revision
                WHERE r.id=? AND r.owner_id=? AND t.created_by_assistant='jarvis'
            """, (rule_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Reminder rule not found")
            if not row["enabled"] or row["deleted_at"] is not None or row["status"] not in ("open", "in_progress") or row["due_at"] is None:
                raise InvalidTransition("Reminder is no longer active")
            if not hmac.compare_digest(token, _token(row)) or _trigger(row) > current:
                raise InvalidTransition("Reminder changed or is not due")
            if row["acknowledged_at"] is not None:
                return {"status": "acknowledged", "already_acknowledged": True}
            timestamp = current.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            conn.execute("INSERT INTO synthetic_reminder_acknowledgements(rule_id,rule_version,schedule_revision,acknowledged_at) VALUES (?,?,?,?)", (rule_id, row["rule_version"], row["schedule_revision"], timestamp))
            self._audit(conn, context, "reminder.acknowledged", rule_id, timestamp)
        return {"status": "acknowledged", "already_acknowledged": False}

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, rule_id: str, now: str) -> None:
        conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,?,'synthetic_reminder',?,'{}',?)", (str(uuid4()), context.owner_id, context.owner_id, event, rule_id, now))
