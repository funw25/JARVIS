"""Synthetic local mail workflows for Jarvis; this module has no send adapter."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context

_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.invalid", re.IGNORECASE)
_TRIAGE = {"new", "needs_reply", "waiting", "archived"}
_PRIORITIES = {"low", "normal", "high"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Mail is available only to Jarvis")


def _address(value: str) -> str:
    value = value.strip()
    if not _ADDRESS.fullmatch(value):
        raise ValueError("Synthetic mail addresses must use the .invalid domain")
    return value


def _recipients(values: list[str]) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= 50:
        raise ValueError("Provide 1 to 50 synthetic recipients")
    return [_address(item) for item in values]


def _text(value: str, maximum: int, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"Invalid {label}")
    return value


def _payload(draft, expires_at: str) -> dict:
    return {"kind": "synthetic_email_send", "draft_id": draft["id"], "mail_id": draft["mail_id"],
            "account": draft["account_address"], "recipients": json.loads(draft["recipients_json"]),
            "subject": draft["subject"], "body": draft["body"], "attachments": [],
            "draft_version": draft["version"], "expires_at": expires_at}


def _serialize(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(payload: dict) -> str:
    return hashlib.sha256(_serialize(payload).encode("utf-8")).hexdigest()


class SyntheticMailService:
    """Jarvis-only local mail, with approval records and no execution mechanism."""

    def create_message(self, context: AssistantContext, sender: str, subject: str, body: str) -> str:
        _require_jarvis(context)
        sender = _address(sender)
        subject = _text(subject, 500, "subject", required=True)
        body = _text(body, 100000, "body")
        item_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("INSERT INTO mail_items(id,owner_id,source_kind,sender,subject,body,triage_status,priority,received_at,updated_at) VALUES (?,?,'synthetic',?,?,?,'new','normal',?,?)",
                         (item_id, context.owner_id, sender, subject, body, now, now))
            self._audit(conn, context, "mail.created", "mail_item", item_id, now)
        return item_id

    def list_messages(self, context: AssistantContext, query: str = "", triage_status: str | None = None) -> list[dict]:
        _require_jarvis(context)
        if triage_status is not None and triage_status not in _TRIAGE:
            raise ValueError("Invalid triage status")
        query = _text(query, 500, "query")
        with connect() as conn:
            rows = conn.execute(
                "SELECT id,sender,subject,triage_status,priority,received_at,updated_at FROM mail_items "
                "WHERE owner_id=? AND (? IS NULL OR triage_status=?) "
                "AND (?='' OR instr(lower(subject),lower(?))>0 OR instr(lower(sender),lower(?))>0 OR instr(lower(body),lower(?))>0) "
                "ORDER BY received_at DESC,id", (context.owner_id, triage_status, triage_status, query, query, query, query)).fetchall()
            return [dict(row) for row in rows]

    def get_message(self, context: AssistantContext, item_id: str) -> dict:
        _require_jarvis(context)
        with connect() as conn:
            row = self._message(conn, context.owner_id, item_id)
            return dict(row)

    def triage(self, context: AssistantContext, item_id: str, triage_status: str, priority: str) -> None:
        _require_jarvis(context)
        if triage_status not in _TRIAGE or priority not in _PRIORITIES:
            raise ValueError("Invalid triage or priority")
        now = _now()
        with connect() as conn:
            self._message(conn, context.owner_id, item_id)
            conn.execute("UPDATE mail_items SET triage_status=?,priority=?,revision=revision+1,updated_at=? WHERE id=? AND owner_id=?",
                         (triage_status, priority, now, item_id, context.owner_id))
            self._audit(conn, context, "mail.triaged", "mail_item", item_id, now)

    def create_draft(self, context: AssistantContext, item_id: str, account: str, recipients: list[str], subject: str, body: str) -> str:
        _require_jarvis(context)
        account, recipients = _address(account), _recipients(recipients)
        subject, body = _text(subject, 500, "subject", required=True), _text(body, 100000, "body")
        draft_id, now = str(uuid4()), _now()
        with connect() as conn:
            self._message(conn, context.owner_id, item_id)
            conn.execute("INSERT INTO mail_drafts(id,owner_id,mail_id,account_address,recipients_json,subject,body,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",
                         (draft_id, context.owner_id, item_id, account, json.dumps(recipients), subject, body, now, now))
            self._audit(conn, context, "mail.draft_created", "mail_draft", draft_id, now)
        return draft_id

    def get_draft(self, context: AssistantContext, draft_id: str) -> dict:
        _require_jarvis(context)
        with connect() as conn:
            row = dict(self._draft(conn, context.owner_id, draft_id))
            row["recipients"] = json.loads(row.pop("recipients_json"))
            return row

    def edit_draft(self, context: AssistantContext, draft_id: str, *, account: str, recipients: list[str], subject: str, body: str) -> None:
        _require_jarvis(context)
        account, recipients = _address(account), _recipients(recipients)
        subject, body = _text(subject, 500, "subject", required=True), _text(body, 100000, "body")
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._draft(conn, context.owner_id, draft_id)
            conn.execute("UPDATE mail_drafts SET account_address=?,recipients_json=?,subject=?,body=?,version=version+1,updated_at=? WHERE id=? AND owner_id=?",
                         (account, json.dumps(recipients), subject, body, now, draft_id, context.owner_id))
            conn.execute("UPDATE action_proposals SET status='invalidated',payload_json='{}' WHERE owner_id=? AND target_ref=? AND kind='synthetic_email_send' AND status IN ('pending','approved')",
                         (context.owner_id, draft_id))
            self._audit(conn, context, "mail.draft_edited", "mail_draft", draft_id, now)

    def prepare_send(self, context: AssistantContext, draft_id: str) -> dict:
        _require_jarvis(context)
        proposal_id, now = str(uuid4()), _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = self._draft(conn, context.owner_id, draft_id)
            conn.execute("UPDATE action_proposals SET status='invalidated',payload_json='{}' WHERE owner_id=? AND target_ref=? AND kind='synthetic_email_send' AND status IN ('pending','approved')", (context.owner_id, draft_id))
            payload = _payload(draft, expires_at)
            payload_hash = _hash(payload)
            conn.execute("INSERT INTO action_proposals(id,owner_id,kind,target_ref,payload_json,payload_hash,status,expires_at,approved_at,executed_at,created_at) VALUES (?,?,'synthetic_email_send',?,?,?,'pending',?,NULL,NULL,?)",
                         (proposal_id, context.owner_id, draft_id, _serialize(payload), payload_hash, expires_at, now))
            self._audit(conn, context, "mail.send_proposed", "action_proposal", proposal_id, now)
        return {"id": proposal_id, "payload": payload, "payload_hash": payload_hash, "status": "pending"}

    def get_proposal(self, context: AssistantContext, proposal_id: str) -> dict:
        _require_jarvis(context)
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = dict(self._proposal(conn, context.owner_id, proposal_id))
            if proposal["status"] in ("pending", "approved") and datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                conn.execute("UPDATE action_proposals SET status='expired',payload_json='{}' WHERE id=? AND owner_id=?", (proposal_id, context.owner_id))
                self._audit(conn, context, "mail.proposal_expired", "action_proposal", proposal_id, _now())
                proposal["status"], proposal["payload_json"] = "expired", "{}"
            proposal["payload"] = json.loads(proposal.pop("payload_json"))
            return proposal

    def approve(self, context: AssistantContext, proposal_id: str, confirmed_hash: str, *, confirmed_by_user: bool = False) -> None:
        _require_jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        now = _now()
        expired = False
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = self._proposal(conn, context.owner_id, proposal_id)
            if proposal["status"] != "pending":
                raise InvalidTransition("Only pending proposals can be approved")
            if datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                conn.execute("UPDATE action_proposals SET status='expired',payload_json='{}' WHERE id=? AND owner_id=?", (proposal_id, context.owner_id))
                self._audit(conn, context, "mail.proposal_expired", "action_proposal", proposal_id, now)
                expired = True
            else:
                draft = self._draft(conn, context.owner_id, proposal["target_ref"])
                current_hash = _hash(_payload(draft, proposal["expires_at"]))
                stored_hash = hashlib.sha256(proposal["payload_json"].encode("utf-8")).hexdigest()
                if not isinstance(confirmed_hash, str) or re.fullmatch(r"[0-9a-f]{64}", confirmed_hash) is None or not hmac.compare_digest(confirmed_hash, proposal["payload_hash"]) or not hmac.compare_digest(stored_hash, proposal["payload_hash"]) or not hmac.compare_digest(current_hash, proposal["payload_hash"]):
                    raise InvalidTransition("Proposal content changed; review a new snapshot")
                conn.execute("UPDATE action_proposals SET status='approved',approved_at=? WHERE id=? AND owner_id=?", (now, proposal_id, context.owner_id))
                self._audit(conn, context, "mail.proposal_approved", "action_proposal", proposal_id, now)
        if expired:
            raise InvalidTransition("Proposal expired")

    def reject(self, context: AssistantContext, proposal_id: str) -> None:
        _require_jarvis(context)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = self._proposal(conn, context.owner_id, proposal_id)
            if proposal["status"] != "pending":
                raise InvalidTransition("Only pending proposals can be rejected")
            conn.execute("UPDATE action_proposals SET status='rejected',payload_json='{}' WHERE id=? AND owner_id=?", (proposal_id, context.owner_id))
            self._audit(conn, context, "mail.proposal_rejected", "action_proposal", proposal_id, now)

    @staticmethod
    def _message(conn, owner_id: str, item_id: str):
        row = conn.execute("SELECT * FROM mail_items WHERE owner_id=? AND id=?", (owner_id, item_id)).fetchone()
        if row is None:
            raise NotFound("Mail item not found")
        return row

    @staticmethod
    def _draft(conn, owner_id: str, draft_id: str):
        row = conn.execute("SELECT * FROM mail_drafts WHERE owner_id=? AND id=?", (owner_id, draft_id)).fetchone()
        if row is None:
            raise NotFound("Mail draft not found")
        return row

    @staticmethod
    def _proposal(conn, owner_id: str, proposal_id: str):
        row = conn.execute("SELECT * FROM action_proposals WHERE owner_id=? AND id=? AND kind='synthetic_email_send'", (owner_id, proposal_id)).fetchone()
        if row is None:
            raise NotFound("Mail proposal not found")
        return row

    @staticmethod
    def _audit(conn, context: AssistantContext, event: str, object_type: str, object_id: str, now: str) -> None:
        conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,?,?,?,'{}',?)",
                     (str(uuid4()), context.owner_id, context.owner_id, event, object_type, object_id, now))
