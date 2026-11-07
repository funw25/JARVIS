"""Synthetic local model consent flow. No network or real model provider is present."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _serialize(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: dict) -> str:
    return hashlib.sha256(_serialize(payload).encode("utf-8")).hexdigest()


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Model suggestions are available only to Jarvis")


class _LocalFakeProvider:
    """Deterministic demonstration provider; output is explicitly not AI-generated."""
    def __init__(self):
        self.calls = 0

    def generate(self, purpose: str, payload: dict) -> str:
        self.calls += 1
        snippet = payload["input"]["body"][:120]
        if purpose == "mail_reply_suggestion":
            return "【本地模拟，非 AI 生成】请在此编写回复。"
        return "【本地模拟，非 AI 生成】" + snippet


class SyntheticModelGateway:
    """User-confirmed preview and suggestion flow for an in-process fake provider."""
    def __init__(self):
        self._provider = _LocalFakeProvider()

    def prepare(self, context: AssistantContext, purpose: str, source_id: str) -> dict:
        _jarvis(context)
        if purpose not in ("mail_summary", "mail_reply_suggestion", "knowledge_summary"):
            raise ValueError("Unsupported model purpose")
        request_id, now = str(uuid4()), _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._sweep_expired(conn, context, now)
            payload = self._build_payload(conn, context, purpose, source_id, expires_at)
            digest = _digest(payload)
            conn.execute("INSERT INTO model_requests(id,owner_id,assistant_id,provider_id,model_id,purpose,source_type,source_id,payload_json,payload_hash,status,expires_at,created_at,completed_at) VALUES (?,?,'jarvis','local_fake','fake-v1',?,?,?,?,?,'pending',?,?,NULL)",
                         (request_id, context.owner_id, purpose, payload["source"]["type"], source_id, _serialize(payload), digest, expires_at, now))
            self._audit(conn, context, "model.preview_created", request_id, now)
        return {"id": request_id, "payload": payload, "payload_hash": digest, "status": "pending"}

    def get_request(self, context: AssistantContext, request_id: str) -> dict:
        _jarvis(context)
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._sweep_expired(conn, context, _now())
            row = dict(self._request(conn, context.owner_id, request_id))
            row.pop("owner_id", None)
            payload = row.pop("payload_json")
            row["payload"] = json.loads(payload) if row["status"] == "pending" else None
            return row

    def cancel(self, context: AssistantContext, request_id: str) -> None:
        _jarvis(context)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = self._request(conn, context.owner_id, request_id)
            if request["status"] != "pending":
                raise InvalidTransition("Only pending requests can be cancelled")
            conn.execute("UPDATE model_requests SET status='cancelled',payload_json='{}' WHERE id=? AND owner_id=?", (request_id, context.owner_id))
            self._audit(conn, context, "model.preview_cancelled", request_id, now)

    def execute(self, context: AssistantContext, request_id: str, confirmed_hash: str, *, confirmed_by_user: bool = False) -> dict:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        now = _now()
        failure = None
        result = None
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            request = self._request(conn, context.owner_id, request_id)
            if request["status"] != "pending":
                raise InvalidTransition("Only pending requests can run")
            if datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                conn.execute("UPDATE model_requests SET status='expired',payload_json='{}' WHERE id=? AND owner_id=?", (request_id, context.owner_id))
                self._audit(conn, context, "model.preview_expired", request_id, now)
                failure = "Request expired"
            else:
                if not isinstance(confirmed_hash, str) or re.fullmatch(r"[0-9a-f]{64}", confirmed_hash) is None or not hmac.compare_digest(confirmed_hash, request["payload_hash"]):
                    raise InvalidTransition("Confirmation does not match the preview")
                stored_hash = hashlib.sha256(request["payload_json"].encode("utf-8")).hexdigest()
                try:
                    current = self._build_payload(conn, context, request["purpose"], request["source_id"], request["expires_at"])
                except NotFound:
                    current = None
                if current is None or not hmac.compare_digest(stored_hash, request["payload_hash"]) or not hmac.compare_digest(_digest(current), request["payload_hash"]):
                    conn.execute("UPDATE model_requests SET status='invalidated',payload_json='{}' WHERE id=? AND owner_id=?", (request_id, context.owner_id))
                    self._audit(conn, context, "model.preview_invalidated", request_id, now)
                    failure = "Source or request changed; review a new preview"
                else:
                    output = self._provider.generate(request["purpose"], current)
                    suggestion_id = str(uuid4())
                    conn.execute("INSERT INTO ai_suggestions(id,owner_id,request_id,kind,source_type,source_id,content,status,created_at) VALUES (?,?,?,?,?,?,?,'proposed',?)",
                                 (suggestion_id, context.owner_id, request_id, request["purpose"], request["source_type"], request["source_id"], output, now))
                    conn.execute("UPDATE model_requests SET status='completed',payload_json='{}',completed_at=? WHERE id=? AND owner_id=?", (now, request_id, context.owner_id))
                    self._audit(conn, context, "model.local_simulation_completed", request_id, now)
                    result = {"suggestion_id": suggestion_id, "content": output, "status": "proposed", "simulated": True}
        if failure:
            raise InvalidTransition(failure)
        return result

    def get_suggestion(self, context: AssistantContext, suggestion_id: str) -> dict:
        _jarvis(context)
        with connect() as conn:
            row = conn.execute("SELECT id,request_id,kind,source_type,source_id,content,status,created_at FROM ai_suggestions WHERE id=? AND owner_id=?", (suggestion_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Suggestion not found")
            if row["source_type"] == "note":
                visible = conn.execute("SELECT 1 FROM knowledge_items WHERE id=? AND owner_id=? AND deleted_at IS NULL", (row["source_id"], context.owner_id)).fetchone()
            else:
                visible = conn.execute("SELECT 1 FROM mail_items WHERE id=? AND owner_id=? AND source_kind='synthetic'", (row["source_id"], context.owner_id)).fetchone()
            if visible is None:
                raise NotFound("Suggestion source not found")
            return dict(row)

    def reject_suggestion(self, context: AssistantContext, suggestion_id: str, *, confirmed_by_user: bool = False) -> None:
        """Reject a proposed local fake-model suggestion; no external actions occur."""
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id,status FROM ai_suggestions WHERE id=? AND owner_id=?", (suggestion_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Suggestion not found")
            if row["status"] != "proposed":
                raise InvalidTransition("Only proposed suggestions can be rejected")
            conn.execute("UPDATE ai_suggestions SET status='rejected',content='' WHERE id=? AND owner_id=?", (suggestion_id, context.owner_id))
            conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,? ,'user',?,'model.suggestion_rejected','ai_suggestion',?,'{}',?)", (str(uuid4()), context.owner_id, context.owner_id, suggestion_id, now))

    @staticmethod
    def _sweep_expired(conn, context: AssistantContext, now: str) -> None:
        rows = conn.execute("SELECT id FROM model_requests WHERE owner_id=? AND assistant_id='jarvis' AND status='pending' AND expires_at<=?", (context.owner_id, now)).fetchall()
        for row in rows:
            conn.execute("UPDATE model_requests SET status='expired',payload_json='{}' WHERE id=? AND owner_id=?", (row["id"], context.owner_id))
            SyntheticModelGateway._audit(conn, context, "model.preview_expired", row["id"], now)

    @staticmethod
    def _build_payload(conn, context: AssistantContext, purpose: str, source_id: str, expires_at: str) -> dict:
        if purpose in ("mail_summary", "mail_reply_suggestion"):
            row = conn.execute("SELECT id,sender,subject,body FROM mail_items WHERE id=? AND owner_id=? AND source_kind='synthetic'", (source_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Synthetic mail not found")
            source_type = "email"
            content = {"subject": row["subject"], "body": row["body"]}
            if purpose == "mail_reply_suggestion":
                content["sender"] = row["sender"]
        elif purpose == "knowledge_summary":
            row = conn.execute("SELECT id,title,body FROM knowledge_items WHERE id=? AND owner_id=? AND deleted_at IS NULL", (source_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Synthetic note not found")
            source_type = "note"
            content = {"title": row["title"], "body": row["body"]}
        else:
            raise ValueError("Unsupported model purpose")
        return {"version": 1, "provider": "local_fake", "model": "fake-v1", "destination": "local_process",
                "leaves_device": False, "purpose": purpose, "source": {"type": source_type, "id": source_id},
                "input": content, "expires_at": expires_at}

    @staticmethod
    def _request(conn, owner_id: str, request_id: str):
        row = conn.execute("SELECT * FROM model_requests WHERE id=? AND owner_id=? AND assistant_id='jarvis' AND provider_id='local_fake'", (request_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Model request not found")
        return row

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, request_id: str, now: str) -> None:
        conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,?,'model_request',?,'{}',?)", (str(uuid4()), context.owner_id, context.owner_id, event, request_id, now))
