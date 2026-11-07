"""Reversible organization of local synthetic mail; no Gmail writes or scheduler."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context
from .synthetic_mail_service import _address, _now, _text, _TRIAGE, _PRIORITIES


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Rules are available only to Jarvis")


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: dict) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class SyntheticMailRuleService:
    def create(self, context: AssistantContext, name: str, *, sender_exact: str | None = None,
               subject_contains: str | None = None, target_triage: str | None = None,
               target_priority: str | None = None, confirmed_by_user: bool = False) -> str:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        name = _text(name, 200, "rule name", required=True).strip()
        sender_exact = _address(sender_exact) if sender_exact is not None else None
        if subject_contains is not None:
            subject_contains = _text(subject_contains, 200, "subject condition", required=True).strip()
        if sender_exact is None and subject_contains is None:
            raise ValueError("At least one rule condition is required")
        if target_triage is None and target_priority is None:
            raise ValueError("At least one local action is required")
        if target_triage is not None and target_triage not in _TRIAGE:
            raise ValueError("Invalid local triage action")
        if target_priority is not None and target_priority not in _PRIORITIES:
            raise ValueError("Invalid local priority action")
        rule_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("INSERT INTO synthetic_mail_rules(id,owner_id,name,sender_exact,subject_contains,target_triage,target_priority,enabled,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,0,1,?,?)",
                         (rule_id, context.owner_id, name, sender_exact, subject_contains, target_triage, target_priority, now, now))
            self._audit(conn, context, "rule.created", "synthetic_rule", rule_id, now)
        return rule_id

    def set_enabled(self, context: AssistantContext, rule_id: str, enabled: bool, *, confirmed_by_user: bool = False) -> None:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        if type(enabled) is not bool:
            raise ValueError("Enabled must be a boolean")
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._rule(conn, context.owner_id, rule_id)
            if bool(current["enabled"]) == enabled:
                return
            conn.execute("UPDATE synthetic_mail_rules SET enabled=?,version=version+1,updated_at=? WHERE id=? AND owner_id=?", (int(enabled), now, rule_id, context.owner_id))
            conn.execute("UPDATE synthetic_rule_previews SET status='invalidated',plan_json='{}' WHERE rule_id=? AND owner_id=? AND status='pending'", (rule_id, context.owner_id))
            self._audit(conn, context, "rule.enabled_changed", "synthetic_rule", rule_id, now)

    def preview(self, context: AssistantContext, rule_id: str) -> dict:
        _jarvis(context)
        now, preview_id = _now(), str(uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rule = self._rule(conn, context.owner_id, rule_id)
            rows = conn.execute("SELECT id,sender,subject,triage_status,priority,revision FROM mail_items WHERE owner_id=? AND source_kind='synthetic' ORDER BY id LIMIT 10001", (context.owner_id,)).fetchall()
            if len(rows) > 10000:
                raise ValueError("Too many synthetic messages to preview")
            items = []
            for row in rows:
                if rule["sender_exact"] is not None and row["sender"].casefold() != rule["sender_exact"].casefold():
                    continue
                if rule["subject_contains"] is not None and rule["subject_contains"].casefold() not in row["subject"].casefold():
                    continue
                before = {"triage_status": row["triage_status"], "priority": row["priority"], "revision": row["revision"]}
                after = {"triage_status": rule["target_triage"] or row["triage_status"], "priority": rule["target_priority"] or row["priority"]}
                if before["triage_status"] != after["triage_status"] or before["priority"] != after["priority"]:
                    items.append({"mail_id": row["id"], "sender": row["sender"], "subject": row["subject"], "before": before, "after": after})
            if len(items) > 100:
                raise ValueError("Rule matches more than 100 messages; narrow its conditions")
            plan = {"rule_id": rule_id, "rule_version": rule["version"], "items": items, "expires_at": expires_at}
            digest = _hash(plan)
            conn.execute("INSERT INTO synthetic_rule_previews(id,owner_id,rule_id,rule_version,plan_json,plan_hash,status,expires_at,created_at,applied_at) VALUES (?,?,?,?,?,?,'pending',?,?,NULL)",
                         (preview_id, context.owner_id, rule_id, rule["version"], _canonical(plan), digest, expires_at, now))
            self._audit(conn, context, "rule.preview_created", "rule_preview", preview_id, now)
        return {"id": preview_id, "plan": plan, "plan_hash": digest, "rule_enabled": bool(rule["enabled"])}

    def apply(self, context: AssistantContext, preview_id: str, confirmed_hash: str, *, confirmed_by_user: bool = False) -> int:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        now, failure, changed = _now(), None, 0
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            preview = self._preview(conn, context.owner_id, preview_id)
            if preview["status"] != "pending":
                raise InvalidTransition("Only pending previews can be applied")
            rule = self._rule(conn, context.owner_id, preview["rule_id"])
            if datetime.fromisoformat(preview["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                conn.execute("UPDATE synthetic_rule_previews SET status='expired',plan_json='{}' WHERE id=? AND owner_id=?", (preview_id, context.owner_id))
                self._audit(conn, context, "rule.preview_expired", "rule_preview", preview_id, now)
                failure = "Rule preview expired"
            elif not rule["enabled"] or rule["version"] != preview["rule_version"]:
                conn.execute("UPDATE synthetic_rule_previews SET status='invalidated',plan_json='{}' WHERE id=? AND owner_id=?", (preview_id, context.owner_id))
                failure = "Rule changed or is disabled"
            elif not isinstance(confirmed_hash, str) or re.fullmatch(r"[0-9a-f]{64}", confirmed_hash) is None or not hmac.compare_digest(confirmed_hash, preview["plan_hash"]) or not hmac.compare_digest(hashlib.sha256(preview["plan_json"].encode("utf-8")).hexdigest(), preview["plan_hash"]):
                raise InvalidTransition("Confirmation does not match the rule preview")
            else:
                plan = json.loads(preview["plan_json"])
                if plan["rule_id"] != rule["id"] or plan["rule_version"] != rule["version"] or plan["expires_at"] != preview["expires_at"]:
                    conn.execute("UPDATE synthetic_rule_previews SET status='invalidated',plan_json='{}' WHERE id=? AND owner_id=?", (preview_id, context.owner_id))
                    failure = "Rule preview changed"
                else:
                    for item in plan["items"]:
                        current = conn.execute("SELECT sender,subject,triage_status,priority,revision FROM mail_items WHERE id=? AND owner_id=? AND source_kind='synthetic'", (item["mail_id"], context.owner_id)).fetchone()
                        if current is None or current["sender"] != item["sender"] or current["subject"] != item["subject"] or current["triage_status"] != item["before"]["triage_status"] or current["priority"] != item["before"]["priority"] or current["revision"] != item["before"]["revision"]:
                            failure = "Mail changed; review a new rule preview"
                            break
                    if failure:
                        conn.execute("UPDATE synthetic_rule_previews SET status='invalidated',plan_json='{}' WHERE id=? AND owner_id=?", (preview_id, context.owner_id))
                        self._audit(conn, context, "rule.preview_invalidated", "rule_preview", preview_id, now)
                    else:
                        for item in plan["items"]:
                            conn.execute("UPDATE mail_items SET triage_status=?,priority=?,revision=revision+1,updated_at=? WHERE id=? AND owner_id=?", (item["after"]["triage_status"], item["after"]["priority"], now, item["mail_id"], context.owner_id))
                            conn.execute("INSERT INTO synthetic_rule_changes(preview_id,mail_id,before_triage,before_priority,after_triage,after_priority,applied_revision,undo_status) VALUES (?,?,?,?,?,?,?,'pending')", (preview_id, item["mail_id"], item["before"]["triage_status"], item["before"]["priority"], item["after"]["triage_status"], item["after"]["priority"], item["before"]["revision"] + 1))
                        changed = len(plan["items"])
                        conn.execute("UPDATE synthetic_rule_previews SET status='applied',plan_json='{}',applied_at=? WHERE id=? AND owner_id=?", (now, preview_id, context.owner_id))
                        self._audit(conn, context, "rule.applied", "rule_preview", preview_id, now)
        if failure:
            raise InvalidTransition(failure)
        return changed

    def undo(self, context: AssistantContext, preview_id: str, *, confirmed_by_user: bool = False) -> dict:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        now, restored, conflicts = _now(), 0, 0
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            preview = self._preview(conn, context.owner_id, preview_id)
            if preview["status"] != "applied":
                raise InvalidTransition("Only applied previews can be undone")
            changes = conn.execute("SELECT * FROM synthetic_rule_changes WHERE preview_id=? AND undo_status='pending'", (preview_id,)).fetchall()
            for change in changes:
                current = conn.execute("SELECT triage_status,priority,revision FROM mail_items WHERE id=? AND owner_id=? AND source_kind='synthetic'", (change["mail_id"], context.owner_id)).fetchone()
                if current is None or current["triage_status"] != change["after_triage"] or current["priority"] != change["after_priority"] or current["revision"] != change["applied_revision"]:
                    conn.execute("UPDATE synthetic_rule_changes SET undo_status='conflict' WHERE preview_id=? AND mail_id=?", (preview_id, change["mail_id"]))
                    conflicts += 1
                else:
                    conn.execute("UPDATE mail_items SET triage_status=?,priority=?,revision=revision+1,updated_at=? WHERE id=? AND owner_id=?", (change["before_triage"], change["before_priority"], now, change["mail_id"], context.owner_id))
                    conn.execute("UPDATE synthetic_rule_changes SET undo_status='undone' WHERE preview_id=? AND mail_id=?", (preview_id, change["mail_id"]))
                    restored += 1
            self._audit(conn, context, "rule.undo_attempted", "rule_preview", preview_id, now)
        return {"restored": restored, "conflicts": conflicts}

    def list_rules(self, context: AssistantContext) -> list[dict]:
        _jarvis(context)
        with connect() as conn:
            rows = conn.execute("SELECT id,name,sender_exact,subject_contains,target_triage,target_priority,enabled,version,created_at,updated_at FROM synthetic_mail_rules WHERE owner_id=? ORDER BY created_at,id LIMIT 10001", (context.owner_id,)).fetchall()
        if len(rows) > 10000:
            raise ValueError("Too many synthetic rules")
        return [dict(row) for row in rows]

    def get_preview(self, context: AssistantContext, preview_id: str) -> dict:
        _jarvis(context)
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._preview(conn, context.owner_id, preview_id)
            state = row["status"]
            if state == "pending" and datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                state = "expired"
                conn.execute("UPDATE synthetic_rule_previews SET status='expired',plan_json='{}' WHERE id=? AND owner_id=?", (preview_id, context.owner_id))
                self._audit(conn, context, "rule.preview_expired", "rule_preview", preview_id, _now())
            result = {"id": row["id"], "rule_id": row["rule_id"], "status": state, "expires_at": row["expires_at"], "applied_at": row["applied_at"]}
            if state == "pending":
                plan_json, digest = row["plan_json"], row["plan_hash"]
                if not hmac.compare_digest(hashlib.sha256(plan_json.encode("utf-8")).hexdigest(), digest):
                    raise InvalidTransition("Rule preview integrity check failed")
                result.update({"plan": json.loads(plan_json), "plan_hash": digest})
            return result

    @staticmethod
    def _rule(conn, owner_id: str, rule_id: str):
        row = conn.execute("SELECT * FROM synthetic_mail_rules WHERE id=? AND owner_id=?", (rule_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Synthetic rule not found")
        return row

    @staticmethod
    def _preview(conn, owner_id: str, preview_id: str):
        row = conn.execute("SELECT * FROM synthetic_rule_previews WHERE id=? AND owner_id=?", (preview_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Synthetic rule preview not found")
        return row

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, object_type: str, object_id: str, now: str) -> None:
        conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,?,?,?,'{}',?)", (str(uuid4()), context.owner_id, context.owner_id, event, object_type, object_id, now))
