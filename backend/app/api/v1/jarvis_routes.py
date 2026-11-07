"""Read-only local Jarvis views over synthetic data; no external integrations."""
from fastapi import APIRouter, Depends, Query, status

from ...security.assistant_context import AssistantContext
from ...services.jarvis_overview_service import JarvisOverviewService
from ...services.jarvis_search_service import JarvisSearchService
from ...services.jarvis_task_query_service import JarvisTaskQueryService
from ...services.synthetic_mail_thread_service import SyntheticMailThreadService
from ...services.synthetic_mail_query_service import SyntheticMailQueryService
from ...services.synthetic_mail_queue_service import SyntheticMailQueueService
from ...services.synthetic_mail_service import SyntheticMailService
from ...services.jarvis_mail_todo_workflow import JarvisMailTodoWorkflow
from ...services.synthetic_knowledge_service import SyntheticKnowledgeService
from ...services.jarvis_knowledge_todo_workflow import JarvisKnowledgeTodoWorkflow
from ...services.synthetic_model_gateway import SyntheticModelGateway
from ...services.jarvis_reply_draft_workflow import JarvisReplyDraftWorkflow
from ...services.synthetic_reminder_service import SyntheticReminderService
from ...services.synthetic_mail_rule_service import SyntheticMailRuleService
from ...services.synthetic_knowledge_tag_service import SyntheticKnowledgeTagService
from ...services.synthetic_knowledge_query_service import SyntheticKnowledgeQueryService
from .routes import context_for, invoke, single_idempotency_key
from .schemas import (SyntheticMailCreate, SyntheticMailTriage, SyntheticDraftWrite, SyntheticProposalApproval, SyntheticSourceTodo,
                      SyntheticCaptureCreate, SyntheticCaptureStatus, SyntheticNoteCreate, SyntheticNoteUpdate,
                      SyntheticModelRequestCreate, SyntheticReminderCreate, SyntheticReminderEnabled, SyntheticReminderAck,
                      SyntheticMailRuleCreate, SyntheticRuleApproval, SyntheticKnowledgeTagWrite)

router = APIRouter(prefix="/jarvis", tags=["jarvis-synthetic"])
overview = JarvisOverviewService()
search_service = JarvisSearchService()
task_search = JarvisTaskQueryService()
threads = SyntheticMailThreadService()
mail_query = SyntheticMailQueryService()
mail_queues = SyntheticMailQueueService()
mail = SyntheticMailService()
mail_todos = JarvisMailTodoWorkflow()
knowledge = SyntheticKnowledgeService()
knowledge_todos = JarvisKnowledgeTodoWorkflow()
fake_model = SyntheticModelGateway()
reply_drafts = JarvisReplyDraftWorkflow()
reminders = SyntheticReminderService()
mail_rules = SyntheticMailRuleService()
knowledge_tags = SyntheticKnowledgeTagService()
knowledge_query = SyntheticKnowledgeQueryService()


@router.get("/overview")
def get_overview(utc_offset_minutes: int = Query(default=0, ge=-720, le=840),
                 limit: int = Query(default=5, ge=1, le=50),
                 ctx: AssistantContext = Depends(context_for)):
    return invoke(overview.snapshot, ctx, utc_offset_minutes=utc_offset_minutes, limit=limit)


@router.get("/search")
def search(query: str = Query(min_length=1, max_length=200),
           kind: list[str] | None = Query(default=None),
           limit: int = Query(default=25, ge=1, le=100),
           ctx: AssistantContext = Depends(context_for)):
    kinds = tuple(kind) if kind is not None else ("mail", "todo", "capture", "note")
    return invoke(search_service.search, ctx, query, kinds=kinds, limit=limit)


@router.get("/tasks")
def search_tasks(query: str = Query(default="", max_length=200),
                 status: list[str] | None = Query(default=None),
                 priority: str | None = Query(default=None),
                 due_from: str | None = Query(default=None),
                 due_before: str | None = Query(default=None),
                 limit: int = Query(default=25, ge=1, le=100),
                 cursor: str | None = Query(default=None, max_length=1024),
                 ctx: AssistantContext = Depends(context_for)):
    statuses = tuple(status) if status is not None else ("open", "in_progress")
    return invoke(task_search.search, ctx, query=query, statuses=statuses, priority=priority,
                  due_from=due_from, due_before=due_before, limit=limit, cursor=cursor)


@router.get("/knowledge/{kind}")
def list_synthetic_knowledge(kind: str, query: str = Query(default="", max_length=200),
                             tag: str | None = Query(default=None, min_length=1, max_length=64),
                             status: str | None = Query(default=None),
                             limit: int = Query(default=25, ge=1, le=100),
                             cursor: str | None = Query(default=None, max_length=1024),
                             ctx: AssistantContext = Depends(context_for)):
    return invoke(knowledge_query.search, ctx, kind, query=query, tag=tag, status=status, limit=limit, cursor=cursor)

@router.get("/mail")
def list_synthetic_mail(query: str = Query(default="", max_length=200),
                        status: list[str] | None = Query(default=None),
                        priority: str | None = Query(default=None),
                        limit: int = Query(default=25, ge=1, le=100),
                        cursor: str | None = Query(default=None, max_length=1024),
                        ctx: AssistantContext = Depends(context_for)):
    statuses = tuple(status) if status is not None else ("new", "needs_reply", "waiting")
    return invoke(mail_query.search, ctx, query=query, statuses=statuses, priority=priority, limit=limit, cursor=cursor)

@router.get("/mail/threads")
def list_mail_threads(limit: int = Query(default=25, ge=1, le=100),
                      ctx: AssistantContext = Depends(context_for)):
    return invoke(threads.list_threads, ctx, limit=limit)


@router.get("/mail/connections/{connection_id}/threads/{thread_id}")
def get_mail_thread(connection_id: str, thread_id: str,
                    limit: int = Query(default=50, ge=1, le=100),
                    ctx: AssistantContext = Depends(context_for)):
    return invoke(threads.get_provider_thread, ctx, connection_id, thread_id, limit=limit)


@router.post("/synthetic/mail", status_code=status.HTTP_201_CREATED)
def create_synthetic_mail(payload: SyntheticMailCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(mail.create_message, ctx, payload.sender, payload.subject, payload.body), "synthetic": True}


@router.get("/synthetic/mail/{mail_id}")
def get_synthetic_mail(mail_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(mail.get_message, ctx, mail_id)
    result.pop("owner_id", None)
    return result


@router.patch("/synthetic/mail/{mail_id}/triage")
def triage_synthetic_mail(mail_id: str, payload: SyntheticMailTriage, ctx: AssistantContext = Depends(context_for)):
    invoke(mail.triage, ctx, mail_id, payload.triage_status, payload.priority)
    return {"status": "updated", "synthetic": True}


@router.post("/synthetic/mail/{mail_id}/tasks", status_code=status.HTTP_201_CREATED)
def synthetic_mail_to_task(mail_id: str, payload: SyntheticSourceTodo,
                           ctx: AssistantContext = Depends(context_for),
                           key: str | None = Depends(single_idempotency_key)):
    return {"id": invoke(mail_todos.create_from_mail, ctx, mail_id, payload.title, payload.description,
                         payload.priority, payload.due_at, confirmed_by_user=True, idempotency_key=key)}


@router.post("/synthetic/mail/{mail_id}/drafts", status_code=status.HTTP_201_CREATED)
def create_synthetic_draft(mail_id: str, payload: SyntheticDraftWrite, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(mail.create_draft, ctx, mail_id, payload.account, payload.recipients, payload.subject, payload.body), "synthetic": True}


@router.get("/synthetic/drafts")
def list_synthetic_drafts(mail_id: str | None = Query(default=None, max_length=128),
                          limit: int = Query(default=25, ge=1, le=100),
                          cursor: str | None = Query(default=None, max_length=1024),
                          ctx: AssistantContext = Depends(context_for)):
    return invoke(mail_queues.drafts, ctx, mail_id=mail_id, limit=limit, cursor=cursor)


@router.get("/synthetic/send-proposals")
def list_pending_synthetic_proposals(limit: int = Query(default=25, ge=1, le=100),
                                     cursor: str | None = Query(default=None, max_length=1024),
                                     ctx: AssistantContext = Depends(context_for)):
    return invoke(mail_queues.pending_proposals, ctx, limit=limit, cursor=cursor)

@router.get("/synthetic/drafts/{draft_id}")
def get_synthetic_draft(draft_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(mail.get_draft, ctx, draft_id)
    result.pop("owner_id", None)
    return result


@router.put("/synthetic/drafts/{draft_id}")
def edit_synthetic_draft(draft_id: str, payload: SyntheticDraftWrite, ctx: AssistantContext = Depends(context_for)):
    invoke(mail.edit_draft, ctx, draft_id, **payload.model_dump())
    return {"status": "updated", "synthetic": True}


@router.post("/synthetic/drafts/{draft_id}/send-proposals", status_code=status.HTTP_201_CREATED)
def prepare_synthetic_send(draft_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(mail.prepare_send, ctx, draft_id)
    return {**result, "execution_available": False}


@router.get("/synthetic/send-proposals/{proposal_id}")
def get_synthetic_proposal(proposal_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(mail.get_proposal, ctx, proposal_id)
    result.pop("owner_id", None)
    return {**result, "execution_available": False}


@router.post("/synthetic/send-proposals/{proposal_id}/approve")
def approve_synthetic_proposal(proposal_id: str, payload: SyntheticProposalApproval, ctx: AssistantContext = Depends(context_for)):
    invoke(mail.approve, ctx, proposal_id, payload.confirmed_hash, confirmed_by_user=True)
    return {"status": "approved", "execution_available": False}


@router.post("/synthetic/send-proposals/{proposal_id}/reject")
def reject_synthetic_proposal(proposal_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(mail.reject, ctx, proposal_id)
    return {"status": "rejected", "execution_available": False}


@router.post("/synthetic/captures", status_code=status.HTTP_201_CREATED)
def create_synthetic_capture(payload: SyntheticCaptureCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(knowledge.create_capture, ctx, payload.title, payload.content, payload.source_url, confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/captures/{capture_id}")
def get_synthetic_capture(capture_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(knowledge.get_capture, ctx, capture_id)
    result.pop("owner_id", None)
    return result


@router.patch("/synthetic/captures/{capture_id}/status")
def change_synthetic_capture_status(capture_id: str, payload: SyntheticCaptureStatus, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.set_capture_status, ctx, capture_id, payload.status)
    return {"status": payload.status, "synthetic": True}


@router.delete("/synthetic/captures/{capture_id}")
def delete_synthetic_capture(capture_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.delete_capture, ctx, capture_id)
    return {"status": "deleted", "recoverable": True}


@router.post("/synthetic/captures/{capture_id}/restore")
def restore_synthetic_capture(capture_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.restore_capture, ctx, capture_id)
    return {"status": "restored", "synthetic": True}


@router.post("/synthetic/notes", status_code=status.HTTP_201_CREATED)
def create_synthetic_note(payload: SyntheticNoteCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(knowledge.create_note, ctx, payload.title, payload.body, payload.source_capture_id, confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/notes/{note_id}")
def get_synthetic_note(note_id: str, ctx: AssistantContext = Depends(context_for)):
    result = invoke(knowledge.get_note, ctx, note_id)
    result.pop("owner_id", None)
    return result


@router.put("/synthetic/notes/{note_id}")
def edit_synthetic_note(note_id: str, payload: SyntheticNoteUpdate, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.edit_note, ctx, note_id, payload.title, payload.body)
    return {"status": "updated", "synthetic": True}


@router.delete("/synthetic/notes/{note_id}")
def delete_synthetic_note(note_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.delete_note, ctx, note_id)
    return {"status": "deleted", "recoverable": True}


@router.post("/synthetic/notes/{note_id}/restore")
def restore_synthetic_note(note_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(knowledge.restore_note, ctx, note_id)
    return {"status": "restored", "synthetic": True}


@router.post("/synthetic/captures/{capture_id}/tasks", status_code=status.HTTP_201_CREATED)
def capture_to_synthetic_task(capture_id: str, payload: SyntheticSourceTodo,
                              ctx: AssistantContext = Depends(context_for),
                              key: str | None = Depends(single_idempotency_key)):
    return {"id": invoke(knowledge_todos.create_from_source, ctx, "capture", capture_id, payload.title,
                         payload.description, payload.priority, payload.due_at, confirmed_by_user=True, idempotency_key=key)}


@router.post("/synthetic/notes/{note_id}/tasks", status_code=status.HTTP_201_CREATED)
def note_to_synthetic_task(note_id: str, payload: SyntheticSourceTodo,
                           ctx: AssistantContext = Depends(context_for),
                           key: str | None = Depends(single_idempotency_key)):
    return {"id": invoke(knowledge_todos.create_from_source, ctx, "note", note_id, payload.title,
                         payload.description, payload.priority, payload.due_at, confirmed_by_user=True, idempotency_key=key)}


@router.post("/synthetic/model/requests", status_code=status.HTTP_201_CREATED)
def prepare_fake_model_request(payload: SyntheticModelRequestCreate, ctx: AssistantContext = Depends(context_for)):
    result = invoke(fake_model.prepare, ctx, payload.purpose, payload.source_id)
    return {**result, "simulated": True}


@router.get("/synthetic/model/requests/{request_id}")
def get_fake_model_request(request_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(fake_model.get_request, ctx, request_id), "simulated": True}


@router.post("/synthetic/model/requests/{request_id}/cancel")
def cancel_fake_model_request(request_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(fake_model.cancel, ctx, request_id)
    return {"status": "cancelled", "simulated": True}


@router.post("/synthetic/model/requests/{request_id}/execute")
def run_fake_model_request(request_id: str, payload: SyntheticProposalApproval, ctx: AssistantContext = Depends(context_for)):
    return invoke(fake_model.execute, ctx, request_id, payload.confirmed_hash, confirmed_by_user=True)


@router.get("/synthetic/model/suggestions/{suggestion_id}")
def get_fake_suggestion(suggestion_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(fake_model.get_suggestion, ctx, suggestion_id), "simulated": True}


@router.post("/synthetic/model/suggestions/{suggestion_id}/reject")
def reject_fake_suggestion(suggestion_id: str, ctx: AssistantContext = Depends(context_for)):
    invoke(fake_model.reject_suggestion, ctx, suggestion_id, confirmed_by_user=True)
    return {"status": "rejected", "simulated": True}


@router.post("/synthetic/model/suggestions/{suggestion_id}/drafts", status_code=status.HTTP_201_CREATED)
def fake_suggestion_to_draft(suggestion_id: str, payload: SyntheticDraftWrite, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(reply_drafts.accept_as_draft, ctx, suggestion_id, **payload.model_dump(), confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/reminders/rules")
def list_synthetic_reminder_rules(ctx: AssistantContext = Depends(context_for)):
    return {"items": invoke(reminders.list_rules, ctx), "synthetic": True}


@router.post("/synthetic/reminders/rules", status_code=status.HTTP_201_CREATED)
def create_synthetic_reminder_rule(payload: SyntheticReminderCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(reminders.create_rule, ctx, payload.todo_id, payload.offset_minutes, confirmed_by_user=True), "synthetic": True}


@router.patch("/synthetic/reminders/rules/{rule_id}/enabled")
def set_synthetic_reminder_enabled(rule_id: str, payload: SyntheticReminderEnabled, ctx: AssistantContext = Depends(context_for)):
    invoke(reminders.set_enabled, ctx, rule_id, payload.enabled, confirmed_by_user=True)
    return {"enabled": payload.enabled, "synthetic": True}


@router.get("/synthetic/reminders/due")
def get_due_synthetic_reminders(limit: int = Query(default=25, ge=1, le=100), ctx: AssistantContext = Depends(context_for)):
    return {**invoke(reminders.due, ctx, limit=limit), "synthetic": True}


@router.post("/synthetic/reminders/{rule_id}/acknowledge")
def acknowledge_synthetic_reminder(rule_id: str, payload: SyntheticReminderAck, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(reminders.acknowledge, ctx, rule_id, payload.token, confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/mail-rules")
def list_synthetic_mail_rules(ctx: AssistantContext = Depends(context_for)):
    return {"items": invoke(mail_rules.list_rules, ctx), "synthetic": True}


@router.post("/synthetic/mail-rules", status_code=status.HTTP_201_CREATED)
def create_synthetic_mail_rule(payload: SyntheticMailRuleCreate, ctx: AssistantContext = Depends(context_for)):
    return {"id": invoke(mail_rules.create, ctx, **payload.model_dump(), confirmed_by_user=True), "synthetic": True}


@router.patch("/synthetic/mail-rules/{rule_id}/enabled")
def set_synthetic_mail_rule_enabled(rule_id: str, payload: SyntheticReminderEnabled, ctx: AssistantContext = Depends(context_for)):
    invoke(mail_rules.set_enabled, ctx, rule_id, payload.enabled, confirmed_by_user=True)
    return {"enabled": payload.enabled, "synthetic": True}


@router.post("/synthetic/mail-rules/{rule_id}/previews", status_code=status.HTTP_201_CREATED)
def create_synthetic_mail_rule_preview(rule_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(mail_rules.preview, ctx, rule_id), "synthetic": True}


@router.get("/synthetic/mail-rule-previews/{preview_id}")
def get_synthetic_mail_rule_preview(preview_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(mail_rules.get_preview, ctx, preview_id), "synthetic": True}


@router.post("/synthetic/mail-rule-previews/{preview_id}/apply")
def apply_synthetic_mail_rule_preview(preview_id: str, payload: SyntheticRuleApproval, ctx: AssistantContext = Depends(context_for)):
    return {"changed": invoke(mail_rules.apply, ctx, preview_id, payload.confirmed_hash, confirmed_by_user=True), "synthetic": True}


@router.post("/synthetic/mail-rule-previews/{preview_id}/undo")
def undo_synthetic_mail_rule_preview(preview_id: str, ctx: AssistantContext = Depends(context_for)):
    return {**invoke(mail_rules.undo, ctx, preview_id, confirmed_by_user=True), "synthetic": True}


@router.get("/synthetic/knowledge-tags/search")
def search_synthetic_knowledge_tags(tag: str = Query(min_length=1, max_length=64),
                                    limit: int = Query(default=25, ge=1, le=100),
                                    ctx: AssistantContext = Depends(context_for)):
    return {**invoke(knowledge_tags.find, ctx, tag, limit=limit), "synthetic": True}


@router.get("/synthetic/knowledge-tags/{kind}/{object_id}")
def list_synthetic_knowledge_tags(kind: str, object_id: str, ctx: AssistantContext = Depends(context_for)):
    return {"items": invoke(knowledge_tags.list_for_item, ctx, kind, object_id), "synthetic": True}


@router.post("/synthetic/knowledge-tags/{kind}/{object_id}")
def add_synthetic_knowledge_tag(kind: str, object_id: str, payload: SyntheticKnowledgeTagWrite,
                                ctx: AssistantContext = Depends(context_for)):
    return {**invoke(knowledge_tags.add, ctx, kind, object_id, payload.tag, confirmed_by_user=True), "synthetic": True}


@router.post("/synthetic/knowledge-tags/{kind}/{object_id}/remove")
def remove_synthetic_knowledge_tag(kind: str, object_id: str, payload: SyntheticKnowledgeTagWrite,
                                   ctx: AssistantContext = Depends(context_for)):
    return {**invoke(knowledge_tags.remove, ctx, kind, object_id, payload.tag, confirmed_by_user=True), "synthetic": True}
