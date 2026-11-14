"""Jarvis synthetic mail endpoints."""
from fastapi import APIRouter
from fastapi import Depends, Query, status
from ...security.assistant_context import AssistantContext
from ...services.synthetic_mail_thread_service import SyntheticMailThreadService
from ...services.synthetic_mail_query_service import SyntheticMailQueryService
from ...services.synthetic_mail_queue_service import SyntheticMailQueueService
from ...services.synthetic_mail_service import SyntheticMailService
from ...services.jarvis_mail_todo_workflow import JarvisMailTodoWorkflow
from .routes import context_for, invoke, single_idempotency_key
from .schemas import SyntheticMailCreate, SyntheticMailTriage, SyntheticDraftWrite, SyntheticProposalApproval, SyntheticSourceTodo

router = APIRouter()
threads = SyntheticMailThreadService()
mail_query = SyntheticMailQueryService()
mail_queues = SyntheticMailQueueService()
mail = SyntheticMailService()
mail_todos = JarvisMailTodoWorkflow()


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
