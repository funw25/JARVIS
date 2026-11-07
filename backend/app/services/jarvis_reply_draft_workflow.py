"""User-confirmed conversion of local simulated reply suggestions to drafts."""
import json
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound, validate_context
from .synthetic_mail_service import SyntheticMailService, _address, _recipients, _text, _now


class JarvisReplyDraftWorkflow:
    def accept_as_draft(self, context: AssistantContext, suggestion_id: str, *, account: str,
                        recipients: list[str], subject: str, body: str, confirmed_by_user: bool = False) -> str:
        validate_context(context)
        if context.assistant_id != "jarvis":
            raise AccessDenied("This workflow is available only to Jarvis")
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        account, recipients = _address(account), _recipients(recipients)
        subject = _text(subject, 500, "subject", required=True)
        body = _text(body, 100000, "body")
        draft_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            suggestion = conn.execute("SELECT id,kind,source_type,source_id,status FROM ai_suggestions WHERE id=? AND owner_id=?",
                                      (suggestion_id, context.owner_id)).fetchone()
            if suggestion is None:
                raise NotFound("Suggestion not found")
            if suggestion["kind"] != "mail_reply_suggestion" or suggestion["source_type"] != "email":
                raise InvalidTransition("Only mail reply suggestions can become drafts")
            if suggestion["status"] != "proposed":
                raise InvalidTransition("Suggestion has already been handled")
            SyntheticMailService._message(conn, context.owner_id, suggestion["source_id"])
            conn.execute("INSERT INTO mail_drafts(id,owner_id,mail_id,account_address,recipients_json,subject,body,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)",
                         (draft_id, context.owner_id, suggestion["source_id"], account, json.dumps(recipients), subject, body, now, now))
            conn.execute("UPDATE ai_suggestions SET status='accepted' WHERE id=? AND owner_id=? AND status='proposed'",
                         (suggestion_id, context.owner_id))
            conn.execute("INSERT INTO suggestion_draft_links(suggestion_id,owner_id,draft_id,created_at) VALUES (?,?,?,?)",
                         (suggestion_id, context.owner_id, draft_id, now))
            conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,'suggestion.accepted_as_draft','suggestion',?,'{}',?)",
                         (str(uuid4()), context.owner_id, context.owner_id, suggestion_id, now))
        return draft_id
